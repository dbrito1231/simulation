"""Replay-benchmark logged LLM decision calls against LM Studio.

Replays requests recorded in a session's lm_studio.jsonl so payload/config
changes get a before/after number instead of ad-hoc eyeballing (the 2026-07-05
qwen-vs-gemma comparison in server.py:41-46 had no repeatable harness).

Modes:
  --as-logged   resend each logged request payload verbatim (baseline)
  --patched     apply the lms_config Phase-2 transforms before sending:
                routine turns get reasoning_effort="none" (the knob this LM
                Studio build actually honors -- chat_template_kwargs.enable_thinking
                and Qwen's /no_think soft switch were both probed 2026-07-11 and
                ignored) plus Qwen non-thinking sampling pins (top_p 0.8,
                top_k 20, min_p 0); invention/sprite turns keep thinking and
                get top_p 0.95 / top_k 20.

Intentionally standalone: imports nothing from simulation/server.py so it can
bench payload shapes that don't exist in the server yet.

Usage:
  uv run python scripts/llm_replay_bench.py --as-logged            # latest session
  uv run python scripts/llm_replay_bench.py --patched --n 40
  uv run python scripts/llm_replay_bench.py --as-logged --session simulation/logs/2026-07-11T21-35-10

Pause the sim server first (POST /control/pause) or its own think traffic
will contend for LM Studio slots and skew latencies.
"""

import argparse
import copy
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGS_DIR = os.path.join(BASE_DIR, "simulation", "logs")
LM_URL = "http://localhost:1234/v1/chat/completions"
LM_MODELS_URL = "http://localhost:1234/v1/models"

# Qwen-recommended sampling pins (see lms_config.md / Qwen model card).
NON_THINKING_SAMPLING = {"top_p": 0.8, "top_k": 20, "min_p": 0}
THINKING_SAMPLING = {"top_p": 0.95, "top_k": 20}


def latest_session_dir():
    dirs = [d for d in os.listdir(LOGS_DIR)
            if os.path.isdir(os.path.join(LOGS_DIR, d))
            and os.path.isfile(os.path.join(LOGS_DIR, d, "lm_studio.jsonl"))]
    if not dirs:
        return None
    return os.path.join(LOGS_DIR, sorted(dirs)[-1])


def load_entries(session_dir, n):
    """Logged decision calls with a full request payload, oldest first."""
    entries = []
    path = os.path.join(session_dir, "lm_studio.jsonl")
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            req = e.get("request")
            if not isinstance(req, dict) or not req.get("messages"):
                continue
            if e.get("sprite_design_only"):
                continue  # rare, image-adjacent; skews the decision metrics
            entries.append(e)
    return entries[:n]


def patch_payload(payload, invention_only):
    """The Phase-2 transform: real thinking control + sampling pins."""
    p = copy.deepcopy(payload)
    p.pop("thinking", None)  # Anthropic-format no-op, dead weight
    if invention_only:
        p.update(THINKING_SAMPLING)
        return p
    p["reasoning_effort"] = "none"
    p.update(NON_THINKING_SAMPLING)
    return p


def extract_decision(text):
    """Brace-depth scan for the first complete JSON object (mirrors the
    server's tolerance for fences/preamble without importing it)."""
    if not text:
        return None
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        break
                    break
        start = text.find("{", start + 1)
    return None


def run_one(entry, mode, timeout):
    payload = entry["request"] if mode == "as-logged" else patch_payload(
        entry["request"], bool(entry.get("invention_only")))
    t0 = time.perf_counter()
    try:
        resp = requests.post(LM_URL, json=payload, timeout=timeout)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        body = resp.json()
    except Exception as exc:  # timeout / connection / bad JSON
        return {
            "agent": entry.get("agent_name"),
            "invention_only": bool(entry.get("invention_only")),
            "error": f"{type(exc).__name__}: {exc}",
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        }
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = (message.get("content") or "").strip()
    reasoning = (message.get("reasoning_content") or "").strip()
    usage = body.get("usage") or {}
    decision = extract_decision(content or reasoning)
    return {
        "agent": entry.get("agent_name"),
        "invention_only": bool(entry.get("invention_only")),
        "error": None if resp.status_code == 200 else f"http {resp.status_code}",
        "latency_ms": latency_ms,
        "finish_reason": choice.get("finish_reason"),
        "thinking_leak": bool(reasoning) if not content else False,
        "reasoning_present": bool(reasoning),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
        "json_valid": decision is not None,
        "action": (decision or {}).get("action"),
    }


def pct(part, whole):
    return f"{100.0 * part / whole:.1f}%" if whole else "n/a"


def summarize(results, label):
    ok = [r for r in results if not r["error"]]
    routine = [r for r in ok if not r["invention_only"]]
    lat = sorted(r["latency_ms"] for r in ok)
    lines = [f"== {label} ({len(results)} calls, {len(results) - len(ok)} errors) =="]
    if lat:
        p90 = lat[min(len(lat) - 1, int(round(0.9 * len(lat))) - 1)] if len(lat) > 1 else lat[0]
        lines.append(f"latency ms: median {int(statistics.median(lat))}  p90 {p90}  "
                     f"mean {int(statistics.mean(lat))}")
    lines.append(f"json valid: {pct(sum(r['json_valid'] for r in ok), len(ok))}")
    lines.append(f"finish_reason=length: {pct(sum(r['finish_reason'] == 'length' for r in ok), len(ok))}")
    lines.append(f"thinking leak (empty content, reasoning_content set): "
                 f"{pct(sum(r['thinking_leak'] for r in routine), len(routine))} of routine")
    rt = [r["reasoning_tokens"] for r in routine if r.get("reasoning_tokens") is not None]
    if rt:
        lines.append(f"routine reasoning tokens: mean {statistics.mean(rt):.0f}")
    actions = {}
    for r in routine:
        if r["action"]:
            actions[r["action"]] = actions.get(r["action"], 0) + 1
    total_actions = sum(actions.values())
    lines.append(f"distinct routine actions: {len(actions)}")
    lines.append(f"move_to_district share: {pct(actions.get('move_to_district', 0), total_actions)}")
    lines.append("action distribution: " + ", ".join(
        f"{a} {c}" for a, c in sorted(actions.items(), key=lambda kv: -kv[1])))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode_g = ap.add_mutually_exclusive_group(required=True)
    mode_g.add_argument("--as-logged", action="store_const", dest="mode", const="as-logged")
    mode_g.add_argument("--patched", action="store_const", dest="mode", const="patched")
    ap.add_argument("--session", help="session log dir (default: latest under simulation/logs)")
    ap.add_argument("--n", type=int, default=100, help="max requests to replay (default 100)")
    ap.add_argument("--workers", type=int, default=2,
                    help="concurrent requests; match LM Studio parallel slots (default 2)")
    ap.add_argument("--timeout", type=int, default=120, help="per-request timeout s")
    ap.add_argument("--out", help="report JSONL path (default simulation/logs/replay_bench/)")
    args = ap.parse_args()

    try:
        models = requests.get(LM_MODELS_URL, timeout=5).json()
        ids = [m.get("id") for m in models.get("data", [])]
    except Exception as exc:
        print(f"LM Studio is not reachable at {LM_MODELS_URL} ({exc}); start it and retry.")
        return 2
    print(f"LM Studio up; models: {ids}")

    session_dir = args.session or latest_session_dir()
    if not session_dir or not os.path.isdir(session_dir):
        print(f"No session dir found ({session_dir!r}).")
        return 2
    entries = load_entries(session_dir, args.n)
    if not entries:
        print(f"No replayable decision entries in {session_dir}.")
        return 2
    print(f"Replaying {len(entries)} logged calls from {session_dir} "
          f"[mode={args.mode}, workers={args.workers}]")

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        results = list(ex.map(lambda e: run_one(e, args.mode, args.timeout), entries))

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    out_path = args.out or os.path.join(LOGS_DIR, "replay_bench",
                                        f"{stamp}_{args.mode}.jsonl")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    summary = summarize(results, f"{args.mode} @ {os.path.basename(session_dir)}")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"summary": summary, "mode": args.mode,
                            "session": session_dir, "n": len(entries)}) + "\n")
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(summary)
    print(f"report: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
