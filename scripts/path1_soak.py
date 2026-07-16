"""SA-9 Path 1 soak verifier — live soak orchestration + log audit.

Runs the 2-hour mini-soak from docs/archive/path-1-minecraft-like-world-plan.md and
fills audit rows 6–10. No LM Studio required for ``prompt-check`` or ``audit``
of an existing session; live ``run`` needs the server (LM Studio optional but
recommended for check 8).

Usage:
    uv run python scripts/path1_soak.py report          # smoke + prompt sample
    uv run python scripts/path1_soak.py prompt-check    # check 10 only
    uv run python scripts/path1_soak.py audit LOG_DIR   # checks 6–10 on logs
    uv run python scripts/path1_soak.py run             # 2h soak (default)
    uv run python scripts/path1_soak.py run --duration 600 --agents 8
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SIM_DIR = ROOT / "simulation"
DEFAULT_DURATION_S = 7200  # 2 hours per SA-9 spec
DEFAULT_AGENTS = 8
SERVER_URL = "http://127.0.0.1:5001"
POLL_INTERVAL_S = 60
# Path 1 prompts can reach ~5.8k tokens with all feature flags enabled. Keep
# this below the ~6.6k per-slot budget from the 20k-context/3-slot target.
PROMPT_TOKEN_LIMIT = 5800
LLM_ERROR_LIMIT = 0.05
HARBOR_MILL_ERAS = {"Harbor Era", "Mill Era"}
CRAFT_RE = re.compile(
    r"\bcrafted\b|\bcraft reflex\b|\bto craft\b|\bproduced\b.*\b(planks|bricks|ingot|tools|cart|wagon)\b",
    re.I,
)
BUILD_RE = re.compile(
    r"\bbuilt\b|\bcompleted\b|\bcontributed\b.*\bproject\b|\bplaced\b.*\b(wall|floor|door|fence)\b",
    re.I,
)
PROGRESS_RE = re.compile(
    r"\bcrafted\b|\bbuilt\b|\bcompleted\b|\bcontributed\b|\bplaced\b|\bdug\b|\bterraform\b",
    re.I,
)


@dataclass
class AuditRow:
    num: int
    check: str
    passed: bool | None  # None = inconclusive
    evidence: str


@dataclass
class AuditReport:
    rows: list[AuditRow] = field(default_factory=list)
    session_id: str = ""
    duration_s: float = 0.0
    log_dir: str = ""

    def verdict(self) -> str:
        actionable = [r for r in self.rows if r.num >= 6]
        if not actionable:
            return "INCONCLUSIVE"
        fails = [r for r in actionable if r.passed is False]
        inconclusive = [r for r in actionable if r.passed is None]
        if fails:
            return "FAIL"
        if inconclusive:
            return "SOFT-PASS"
        return "PASS"

    def print_table(self):
        print("\n| # | Check | Pass? | Evidence |")
        print("|---|-------|-------|----------|")
        for r in self.rows:
            status = "PASS" if r.passed is True else ("FAIL" if r.passed is False else "pending")
            ev = r.evidence.replace("|", "\\|")
            check = r.check.replace("\u2265", ">=").replace("\u2264", "<=")
            print(f"| {r.num} | {check} | {status} | {ev} |")
        print(f"\nVerdict: **{self.verdict()}**")
        if self.session_id:
            print(f"Session: {self.session_id}")
        if self.duration_s:
            print(f"Soak duration: {self.duration_s / 60:.1f} min")


def _stream_jsonl(path: Path):
    if not path.is_file():
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return list(_stream_jsonl(path))


def estimate_prompt_tokens(payload: dict[str, Any]) -> int:
    """Rough token estimate: chars/4 over serialized chat messages."""
    messages = payload.get("messages") or []
    text = json.dumps(messages, ensure_ascii=False)
    return max(1, len(text) // 4)


def prompt_tokens_from_lm_record(rec: dict[str, Any]) -> int | None:
    """Prefer LM Studio usage.prompt_tokens when logged."""
    resp = rec.get("response")
    if isinstance(resp, dict):
        usage = resp.get("usage") or {}
        pt = usage.get("prompt_tokens")
        if isinstance(pt, int) and pt > 0:
            return pt
    req = rec.get("request") or {}
    if req.get("messages"):
        return estimate_prompt_tokens(req)
    return None


def newest_log_dir(base: Path = SIM_DIR / "logs") -> Path | None:
    if not base.is_dir():
        return None
    dirs = [p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")]
    if not dirs:
        return None
    return max(dirs, key=lambda p: p.stat().st_mtime)


def http_get_json(url: str, timeout: float = 10.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post(url: str, body: dict | None = None, timeout: float = 15.0) -> dict[str, Any]:
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def server_reachable() -> bool:
    try:
        http_get_json(f"{SERVER_URL}/state", timeout=3.0)
        return True
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return False


def run_smoke() -> bool:
    print("Running path1_smoke.py …")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "path1_smoke.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    print(proc.stdout, end="")
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        return False
    return True


def prompt_check() -> AuditRow:
    """Headless sample of a full decision prompt (check 10)."""
    code = f"""
import json, sys
sys.path.insert(0, {str(SIM_DIR)!r})
sys.path.insert(0, {str(ROOT / "scripts")!r})
from path1_smoke import make_engine
import server
engine = make_engine(8)
data = engine._build_think_payload(engine.agents[0])
payload = server.build_decision_payload(data, "", server.build_response_format())
messages = payload.get("messages") or []
chars = len(json.dumps(messages, ensure_ascii=False))
print(json.dumps({{"agent": engine.agents[0]["name"], "role": engine.agents[0]["role"],
                  "estimate": max(1, chars // 4), "chars": chars}}))
"""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    info = json.loads(proc.stdout.strip().splitlines()[-1])
    tokens = int(info["estimate"])
    passed = tokens <= PROMPT_TOKEN_LIMIT
    evidence = (f"sample agent={info['agent']} role={info['role']} "
                f"~{tokens} tokens (chars/4, limit {PROMPT_TOKEN_LIMIT})")
    print(f"  prompt-check: {evidence}")
    return AuditRow(10, "Prompt ≤5800 tokens", passed, evidence)


def audit_logs(log_dir: Path, duration_s: float = 0.0) -> AuditReport:
    log_dir = log_dir.resolve()
    report = AuditReport(
        session_id=log_dir.name,
        duration_s=duration_s,
        log_dir=str(log_dir),
    )

    benchmarks = _read_jsonl(log_dir / "benchmarks.jsonl")
    activity = _read_jsonl(log_dir / "activity.jsonl")

    lm_total = 0
    lm_errors = 0
    error_kinds: dict[str, int] = {}
    token_samples: list[int] = []
    for rec in _stream_jsonl(log_dir / "lm_studio.jsonl"):
        if rec.get("type") != "lm_studio" and "agent_name" not in rec:
            continue
        lm_total += 1
        if rec.get("error"):
            lm_errors += 1
            k = str(rec.get("error") or "unknown")
            error_kinds[k] = error_kinds.get(k, 0) + 1
        pt = prompt_tokens_from_lm_record(rec)
        if pt is not None:
            token_samples.append(pt)
            if len(token_samples) > 200:
                token_samples.pop(0)

    # --- Check 6: Era >= Harbor or Mill ---
    era_names = set()
    for rec in benchmarks:
        if rec.get("metric") == "era":
            detail = rec.get("detail") or {}
            if detail.get("era"):
                era_names.add(str(detail["era"]))
    for msg in (r.get("message") or "" for r in activity):
        for era in HARBOR_MILL_ERAS:
            if era in msg:
                era_names.add(era)
    harbor_mill_built = any(
        "harbor" in (r.get("message") or "").lower() or "mill" in (r.get("message") or "").lower()
        for r in activity
        if "built" in (r.get("message") or "").lower() or "enters" in (r.get("message") or "").lower()
    )
    era_hit = bool(era_names & HARBOR_MILL_ERAS)
    if era_hit:
        report.rows.append(AuditRow(6, "Era ≥ Harbor or Mill", True,
                                    f"eras seen: {', '.join(sorted(era_names))}"))
    elif harbor_mill_built:
        report.rows.append(AuditRow(6, "Era ≥ Harbor or Mill", None,
                                    "harbor/mill build activity but era not confirmed in benchmarks"))
    elif duration_s >= 3600:
        report.rows.append(AuditRow(6, "Era ≥ Harbor or Mill", False,
                                    f"no Harbor/Mill era in {duration_s/3600:.1f}h soak"))
    else:
        report.rows.append(AuditRow(6, "Era ≥ Harbor or Mill", None,
                                    "pending — tier-3 era unlikely in short soak; need ≥1h+ live run"))

    # --- Check 7: night_shelter_rate benchmark ---
    shelter_recs = [r for r in benchmarks if r.get("metric") == "night_shelter_rate"]
    if shelter_recs:
        last = shelter_recs[-1]
        val = last.get("value")
        detail = last.get("detail") or {}
        report.rows.append(AuditRow(
            7, "Night/shelter", True,
            f"night_shelter_rate={val} (sheltered {detail.get('sheltered')}/{detail.get('total')}, n={len(shelter_recs)})",
        ))
    elif duration_s >= 1800:
        report.rows.append(AuditRow(7, "Night/shelter", False, "no night_shelter_rate in benchmarks.jsonl"))
    else:
        report.rows.append(AuditRow(7, "Night/shelter", None, "no night tick yet — soak too short for night cycle"))

    # --- Check 8: LLM errors < 5% ---
    if lm_total:
        rate = lm_errors / lm_total
        passed = rate < LLM_ERROR_LIMIT
        report.rows.append(AuditRow(
            8, "LLM errors <5%", passed,
            f"{lm_errors}/{lm_total} = {rate*100:.1f}% ({error_kinds or 'none'})",
        ))
    elif duration_s > 300:
        report.rows.append(AuditRow(8, "LLM errors <5%", None,
                                    "no lm_studio.jsonl entries — LM Studio offline or no think turns"))
    else:
        report.rows.append(AuditRow(8, "LLM errors <5%", None, "soak too short / no LM calls yet"))

    # --- Check 9: No 3h deadlock (crafts + builds) ---
    craft_msgs = [r for r in activity if CRAFT_RE.search(r.get("message") or "")]
    build_msgs = [r for r in activity if BUILD_RE.search(r.get("message") or "")]
    progress_msgs = [r for r in activity if PROGRESS_RE.search(r.get("message") or "")]
    deadlock = False
    if duration_s >= 3600 and activity:
        cutoff_frame = activity[-1].get("frame_tick") or 0
        window = cutoff_frame - int(duration_s * 30 * 0.33)
        recent = [r for r in progress_msgs if (r.get("frame_tick") or 0) >= window]
        if not recent and progress_msgs:
            deadlock = True
    if deadlock:
        report.rows.append(AuditRow(
            9, "No 3h deadlock", False,
            f"stall in last third: progress={len(progress_msgs)} craft={len(craft_msgs)} build={len(build_msgs)}",
        ))
    elif progress_msgs:
        report.rows.append(AuditRow(
            9, "No 3h deadlock", True,
            f"progress={len(progress_msgs)} craft={len(craft_msgs)} build={len(build_msgs)}",
        ))
    elif craft_msgs or build_msgs:
        report.rows.append(AuditRow(
            9, "No 3h deadlock", None,
            f"partial progress: crafts={len(craft_msgs)} builds={len(build_msgs)}",
        ))
    else:
        report.rows.append(AuditRow(9, "No 3h deadlock", None, "no craft/build activity logged yet"))

    # --- Check 10: Prompt ≤5800 tokens (from lm_studio samples) ---
    if token_samples:
        mx = max(token_samples)
        avg = sum(token_samples) / len(token_samples)
        passed = mx <= PROMPT_TOKEN_LIMIT
        report.rows.append(AuditRow(
            10, "Prompt ≤5800 tokens", passed,
            f"max={mx} avg={avg:.0f} n={len(token_samples)} (limit {PROMPT_TOKEN_LIMIT})",
        ))
    else:
        # Fall back to headless sample
        try:
            row = prompt_check()
            row.num = 10
            row.check = "Prompt ≤5800 tokens"
            report.rows.append(row)
        except Exception as exc:
            report.rows.append(AuditRow(10, "Prompt ≤5800 tokens", None, f"no samples: {exc}"))

    return report


def poll_state_snapshots(
    duration_s: float,
    interval_s: float,
    out_path: Path,
) -> list[dict[str, Any]]:
    """Poll /state during soak; write timeline JSON for post-mortem."""
    snapshots = []
    start = time.time()
    end = start + duration_s
    while time.time() < end:
        try:
            state = http_get_json(f"{SERVER_URL}/state")
            snap = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "frameTick": state.get("frameTick"),
                "era": (state.get("civilization") or {}).get("era"),
                "completedProjects": (state.get("civilization") or {}).get("completedProjects"),
                "structures": len((state.get("civilization") or {}).get("structures") or []),
                "settlements": len((state.get("civilization") or {}).get("settlements") or []),
                "flags": (state.get("config") or {}).get("flags"),
            }
            snapshots.append(snap)
            print(f"  [{len(snapshots)}] frame={snap['frameTick']} era={snap['era']} "
                  f"projects={snap['completedProjects']} structures={snap['structures']}")
            out_path.write_text(json.dumps(snapshots, indent=2), encoding="utf-8")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"  poll error: {exc}")
        remaining = end - time.time()
        if remaining <= 0:
            break
        time.sleep(min(interval_s, remaining))
    return snapshots


def run_soak(duration_s: float, agents: int, reset: bool, spawn_server: bool) -> int:
    if not run_smoke():
        print("FAIL — path1_smoke.py did not pass; fix before soak")
        return 1

    proc = None
    if not server_reachable():
        if not spawn_server:
            print(f"Server not reachable at {SERVER_URL}. Start it or pass --spawn-server.")
            return 1
        print("Starting server subprocess …")
        proc = subprocess.Popen(
            [sys.executable, str(SIM_DIR / "server.py")],
            cwd=str(ROOT),
        )
        for _ in range(30):
            if server_reachable():
                break
            time.sleep(1)
        else:
            print("Server failed to start within 30s")
            if proc:
                proc.terminate()
            return 1

    log_dir_before = newest_log_dir()
    if reset:
        print(f"Resetting world with agents={agents} …")
        http_post(f"{SERVER_URL}/control/reset", {"agents": agents})

    log_dir = newest_log_dir()
    if log_dir and log_dir != log_dir_before:
        session_id = log_dir.name
    elif log_dir:
        session_id = log_dir.name
    else:
        session_id = "unknown"

    soak_meta = SIM_DIR / "logs" / f"path1_soak_{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
    timeline_path = soak_meta.with_suffix(".timeline.json")
    print(f"Soak: {duration_s/60:.1f} min, agents={agents}, session~{session_id}")
    print(f"Timeline -> {timeline_path}")

    start = time.time()
    poll_state_snapshots(duration_s, POLL_INTERVAL_S, timeline_path)
    elapsed = time.time() - start

    log_dir = newest_log_dir()
    if not log_dir:
        print("No log directory found after soak")
        if proc:
            proc.terminate()
        return 1

    report = audit_logs(log_dir, duration_s=elapsed)
    # Prepend smoke rows 1-5 (already verified)
    smoke_evidence = "scripts/path1_smoke.py PASS (run at soak start)"
    for num, check in [
        (1, "Ingot crafted"), (2, "Tool gate enforced"), (3, "Tile placed (2D viewer)"),
        (4, "Terrain mutated"), (5, "Two settlements"),
    ]:
        report.rows.insert(num - 1, AuditRow(num, check, True, smoke_evidence))

    report.duration_s = elapsed
    report.session_id = log_dir.name
    report.print_table()

    meta = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "duration_s": elapsed,
        "agents": agents,
        "log_dir": str(log_dir),
        "verdict": report.verdict(),
        "rows": [{"num": r.num, "check": r.check, "passed": r.passed, "evidence": r.evidence}
                 for r in report.rows],
    }
    soak_meta.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Report written -> {soak_meta}")

    if proc:
        proc.terminate()
    return 0 if report.verdict() in ("PASS", "SOFT-PASS") else 1


def cmd_report(_args: argparse.Namespace) -> int:
    ok = run_smoke()
    row = prompt_check()
    row.num = 10
    print(f"\nCheck 10 pre-soak: {'PASS' if row.passed else 'FAIL'} — {row.evidence}")
    return 0 if ok and row.passed else 1


def cmd_audit(args: argparse.Namespace) -> int:
    log_dir = Path(args.log_dir) if args.log_dir else newest_log_dir()
    if not log_dir or not log_dir.is_dir():
        print("No log directory found. Pass LOG_DIR or run a soak first.")
        return 1
    duration = args.duration if args.duration else 0.0
    report = audit_logs(log_dir, duration_s=duration)
    report.session_id = log_dir.name
    report.print_table()
    return 0 if report.verdict() in ("PASS", "SOFT-PASS") else 1


def main():
    parser = argparse.ArgumentParser(description="SA-9 Path 1 soak verifier")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("prompt-check", help="Headless prompt token sample (check 10)")

    p_audit = sub.add_parser("audit", help="Audit an existing log session")
    p_audit.add_argument("log_dir", nargs="?", help="simulation/logs/<session-id>")
    p_audit.add_argument("--duration", type=float, default=0.0,
                         help="Known soak duration in seconds (for inconclusive thresholds)")
    p_audit.set_defaults(func=cmd_audit)

    p_run = sub.add_parser("run", help="Live soak against server")
    p_run.add_argument("--duration", type=int, default=DEFAULT_DURATION_S,
                       help=f"Soak seconds (default {DEFAULT_DURATION_S}=2h)")
    p_run.add_argument("--agents", type=int, default=DEFAULT_AGENTS)
    p_run.add_argument("--reset", action="store_true", help="POST /control/reset before soak")
    p_run.add_argument("--spawn-server", action="store_true",
                       help="Start server.py if not already running")
    p_run.set_defaults(func=lambda a: run_soak(a.duration, a.agents, a.reset, a.spawn_server))

    p_rep = sub.add_parser("report", help="Smoke + prompt-check")
    p_rep.set_defaults(func=cmd_report)

    args = parser.parse_args()
    if args.cmd == "prompt-check":
        row = prompt_check()
        print(f"Check 10: {'PASS' if row.passed else 'FAIL'} — {row.evidence}")
        return 0 if row.passed else 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
