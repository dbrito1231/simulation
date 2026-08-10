"""F2 Theory of Mind contention gate — matched flag-off vs flag-on native soaks.

Orchestrates two native ``simulation/server.py`` runs (ToM off, then
``SIM_THEORY_OF_MIND=1``), each observed by ``soak_monitor.py``. Refuses if
Docker ``gitserv-sim`` is running, any native server/soak harness is active,
or the target port is already served.

Starts the server with ``sys.executable`` (not ``uv run``) so stop can kill the
full process tree. On Windows, ``uv run`` orphans the child ``python.exe`` when
only the wrapper is terminated — see ``specs/12-ops.md``. ``stop_server`` uses
``taskkill /T /F`` (Windows) or process-group signals (Unix) and sweeps any
remaining ``simulation/server.py`` PIDs before the next phase.

Before each soak phase the script waits until ``/state`` is unreachable (port
released after the prior server stops). After start it hard-asserts
``config.flags.THEORY_OF_MIND_ENABLED`` matches the phase expectation via
``/state`` (and ties readiness to a new log session) before ``soak_monitor``
runs. A final cleanup helper kills all native sim servers on the target port on
exit or failure.

Usage:
    uv run python scripts/tom_contention_soak.py [--minutes 45] [--port 5001]
    uv run python scripts/tom_contention_soak.py --flagon-only [--minutes 45]

    ``--flagon-only`` skips the baseline phase, requires existing
    ``simulation/logs/soak-tom-baseline.json``, reruns only the flag-on phase,
    and rewrites ``tom-contention-soak-result.json`` merging preserved baseline
    data with the new flagon summary and comparison.

Outputs:
    simulation/logs/soak-tom-baseline.json
    simulation/logs/soak-tom-flagon.json
    simulation/logs/tom-contention-soak-result.json
    simulation/logs/tom-contention-soak.log (when stdout redirected)
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import signal
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
BASELINE_JSON = LOG_ROOT / "soak-tom-baseline.json"
FLAGON_JSON = LOG_ROOT / "soak-tom-flagon.json"
PROGRESS_LOG = LOG_ROOT / "tom-contention-soak.log"
SOAK_MONITOR = ROOT / "scripts" / "soak_monitor.py"
SERVER_SCRIPT = ROOT / "simulation" / "server.py"
DOCKER_NAME = "gitserv-sim"
BASELINE_LABEL = "tom-baseline"
FLAGON_LABEL = "tom-flagon"
PORT_DOWN_TIMEOUT_S = 90.0
READY_TIMEOUT_S = 120.0
_CLEANUP_PORT: int | None = None


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


def tom_flag_from_state(state: dict) -> bool | None:
    flags = (state.get("config") or {}).get("flags") or {}
    value = flags.get("THEORY_OF_MIND_ENABLED")
    if value is None:
        return None
    return bool(value)


def wait_for_port_down(port: int, timeout_s: float) -> None:
    """Block until /state is unreachable; hard-fail on timeout."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not port_served(port):
            log(f"port {port} released (/state unreachable)")
            return
        time.sleep(0.5)
    raise SystemExit(
        f"timeout: port {port} still serves /state after {timeout_s:.0f}s — "
        "refusing to start next phase on a stale server",
    )


def assert_tom_flag(port: int, expected: bool) -> None:
    state = http_get_state(port)
    if state is None:
        raise SystemExit(f"assert failed: /state unreachable on port {port}")
    actual = tom_flag_from_state(state)
    if actual != expected:
        raise SystemExit(
            f"assert failed: THEORY_OF_MIND_ENABLED={actual!r} on port {port}, "
            f"expected {expected!r}",
        )
    log(f"ASSERT PASS: THEORY_OF_MIND_ENABLED={actual} on port {port}")


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


def _powershell_python_pids(pattern: str) -> list[int]:
    ps_cmd = (
        "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | "
        f"Where-Object {{ $_.CommandLine -match '{pattern}' }} | "
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


def native_server_pids() -> list[int]:
    if sys.platform == "win32":
        ps_cmd = (
            "simulation[\\\\/]server\\.py|simulation\\.server"
        )
        return _powershell_python_pids(ps_cmd)
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


def soak_harness_pids() -> list[int]:
    """PIDs for tom_contention_soak / soak_monitor (excluding this process tree)."""
    exclude = {os.getpid(), os.getppid()}
    if sys.platform == "win32":
        pattern = "tom_contention_soak|soak_monitor"
        pids = _powershell_python_pids(pattern)
    else:
        try:
            proc = subprocess.run(
                ["pgrep", "-f", "tom_contention_soak|soak_monitor"],
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
    return sorted({pid for pid in pids if pid not in exclude})


def port_listener_pids(port: int) -> list[int]:
    if sys.platform == "win32":
        try:
            proc = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
        pids: list[int] = []
        needle = f":{port}"
        for line in (proc.stdout or "").splitlines():
            if needle in line and "LISTENING" in line.upper():
                parts = line.split()
                if parts and parts[-1].isdigit():
                    pids.append(int(parts[-1]))
        return sorted(set(pids))
    try:
        proc = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
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
    return sorted(set(pids))


def kill_process_tree(pid: int) -> None:
    if pid <= 0:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            timeout=30,
            check=False,
        )
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            return
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.2)
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def cleanup_all_native_sim_servers(port: int) -> None:
    """Kill native simulation servers and port listeners (exit/failure helper)."""
    targets = sorted(set(native_server_pids()) | set(port_listener_pids(port)))
    if not targets:
        return
    log(f"cleanup: killing native sim server pids={targets} on port={port}")
    for pid in targets:
        kill_process_tree(pid)
    deadline = time.time() + PORT_DOWN_TIMEOUT_S
    while time.time() < deadline:
        if not port_served(port) and not port_listener_pids(port):
            log(f"cleanup: port {port} released")
            return
        time.sleep(0.5)
    log(f"cleanup: warning — port {port} may still be served after kill sweep")


def _register_cleanup(port: int) -> None:
    global _CLEANUP_PORT
    _CLEANUP_PORT = port


def _atexit_cleanup() -> None:
    if _CLEANUP_PORT is None:
        return
    try:
        cleanup_all_native_sim_servers(_CLEANUP_PORT)
    except Exception as exc:
        # Avoid subprocess/thread errors during interpreter shutdown.
        print(f"[atexit] cleanup skipped: {exc}", flush=True)


atexit.register(_atexit_cleanup)


def session_dirs() -> list[Path]:
    if not LOG_ROOT.is_dir():
        return []
    return [
        p for p in LOG_ROOT.iterdir()
        if p.is_dir() and (p / "llm.jsonl").exists() and (p / "benchmarks.jsonl").exists()
    ]


def wait_for_ready(
    port: int,
    before_names: set[str],
    expected_tom: bool,
    timeout_s: float,
) -> Path:
    """Wait for a new session and /state flag matching expected_tom."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = http_get_state(port)
        candidates = [p for p in session_dirs() if p.name not in before_names]
        if state is not None and candidates:
            actual = tom_flag_from_state(state)
            if actual == expected_tom:
                session = max(candidates, key=lambda p: p.stat().st_mtime)
                log(
                    f"ASSERT PASS: new session {session.name} with "
                    f"THEORY_OF_MIND_ENABLED={actual}",
                )
                return session
            if actual is not None:
                log(
                    f"/state reachable but THEORY_OF_MIND_ENABLED={actual} "
                    f"(expected {expected_tom}), waiting for new server",
                )
        time.sleep(1)
    raise SystemExit(
        f"timeout waiting for new session with THEORY_OF_MIND_ENABLED={expected_tom} "
        f"after {timeout_s:.0f}s on port {port}",
    )


def start_server(port: int, tom_env: str | None) -> subprocess.Popen:
    env = os.environ.copy()
    env["SIM_PORT"] = str(port)
    if tom_env is None:
        env.pop("SIM_THEORY_OF_MIND", None)
    else:
        env["SIM_THEORY_OF_MIND"] = tom_env
    python_exe = sys.executable
    log(
        f"starting server pid-target={python_exe} port={port} "
        f"SIM_THEORY_OF_MIND={env.get('SIM_THEORY_OF_MIND', '(unset)')}",
    )
    popen_kwargs: dict = {
        "cwd": str(ROOT),
        "env": env,
    }
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        [python_exe, str(SERVER_SCRIPT)],
        **popen_kwargs,
    )
    return proc


def stop_server(proc: subprocess.Popen | None, port: int) -> None:
    if proc is not None and proc.poll() is None:
        log(f"stopping server tree pid={proc.pid}")
        kill_process_tree(proc.pid)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            log(f"force-kill server pid={proc.pid}")
            kill_process_tree(proc.pid)
            proc.wait(timeout=10)
    orphans = sorted(set(native_server_pids()) | set(port_listener_pids(port)))
    if orphans:
        log(f"sweeping orphan sim server pids={orphans}")
        for pid in orphans:
            kill_process_tree(pid)


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
    server_pids = native_server_pids()
    if server_pids:
        raise SystemExit(
            f"refusing: native simulation/server.py already running (pids={server_pids})"
        )
    harness_pids = soak_harness_pids()
    if harness_pids:
        raise SystemExit(
            f"refusing: tom_contention_soak/soak_monitor already running (pids={harness_pids})"
        )
    listeners = port_listener_pids(port)
    if listeners:
        raise SystemExit(
            f"refusing: port {port} has LISTENING pids={listeners}"
        )
    if port_served(port):
        raise SystemExit(f"refusing: port {port} already serves /state")


def run_phase(
    label: str,
    port: int,
    minutes: float,
    tom_env: str | None,
    expected_tom: bool,
) -> tuple[dict, str]:
    wait_for_port_down(port, PORT_DOWN_TIMEOUT_S)
    before_names = {p.name for p in session_dirs()}
    proc = start_server(port, tom_env)
    try:
        session = wait_for_ready(port, before_names, expected_tom, READY_TIMEOUT_S)
        assert_tom_flag(port, expected_tom)
        rc = run_soak_monitor(label, minutes)
        if rc != 0:
            log(f"warning: soak_monitor returned {rc} for label={label}")
        summary = read_soak_summary(label)
        if summary is None:
            raise SystemExit(f"missing soak summary for label={label}")
        return summary, session.name
    finally:
        stop_server(proc, port)
        wait_for_port_down(port, PORT_DOWN_TIMEOUT_S)


def load_baseline_for_flagon_only() -> tuple[dict, str]:
    if not BASELINE_JSON.is_file():
        raise SystemExit(
            f"--flagon-only requires existing baseline summary at {BASELINE_JSON}",
        )
    baseline = json.loads(BASELINE_JSON.read_text(encoding="utf-8"))
    baseline_session = baseline.get("session")
    if not baseline_session:
        if RESULT_PATH.is_file():
            prior = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
            baseline_session = (prior.get("sessions") or {}).get("baseline")
        if not baseline_session:
            raise SystemExit(
                f"--flagon-only: could not determine baseline session from "
                f"{BASELINE_JSON} or {RESULT_PATH}",
            )
    log(f"--flagon-only: preserving baseline session={baseline_session}")
    return baseline, baseline_session


def write_result(
    *,
    started_at: str,
    minutes: float,
    port: int,
    baseline_session: str,
    flagon_session: str,
    baseline_summary: dict,
    flagon_summary: dict,
    flagon_only: bool,
) -> None:
    result = {
        "started_at": started_at,
        "ended_at": utc_now(),
        "minutes_per_phase": minutes,
        "port": port,
        "flagon_only_rerun": flagon_only,
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, default=45, help="minutes per phase (default: 45)")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("SIM_PORT", "5001")),
        help="server port (default: SIM_PORT or 5001)",
    )
    parser.add_argument(
        "--flagon-only",
        action="store_true",
        help="skip baseline; require soak-tom-baseline.json; rerun flag-on phase only",
    )
    args = parser.parse_args()
    if args.minutes <= 0:
        raise SystemExit("--minutes must be positive")

    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    _register_cleanup(args.port)
    started_at = utc_now()
    mode = "flagon-only" if args.flagon_only else "full"
    log(f"tom contention soak starting mode={mode} minutes={args.minutes} port={args.port}")

    preflight(args.port)

    try:
        if args.flagon_only:
            baseline_summary, baseline_session = load_baseline_for_flagon_only()
            flagon_summary, flagon_session = run_phase(
                FLAGON_LABEL, args.port, args.minutes, "1", True,
            )
            write_result(
                started_at=started_at,
                minutes=args.minutes,
                port=args.port,
                baseline_session=baseline_session,
                flagon_session=flagon_session,
                baseline_summary=baseline_summary,
                flagon_summary=flagon_summary,
                flagon_only=True,
            )
            return 0

        baseline_summary, baseline_session = run_phase(
            BASELINE_LABEL, args.port, args.minutes, None, False,
        )
        flagon_summary, flagon_session = run_phase(
            FLAGON_LABEL, args.port, args.minutes, "1", True,
        )
        write_result(
            started_at=started_at,
            minutes=args.minutes,
            port=args.port,
            baseline_session=baseline_session,
            flagon_session=flagon_session,
            baseline_summary=baseline_summary,
            flagon_summary=flagon_summary,
            flagon_only=False,
        )
        return 0
    finally:
        cleanup_all_native_sim_servers(args.port)


if __name__ == "__main__":
    raise SystemExit(main())
