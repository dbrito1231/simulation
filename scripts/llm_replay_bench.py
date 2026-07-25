"""Replay-benchmark logged LLM decision calls against Ollama's native
/api/chat endpoint.

Ported to Ollama (2026-07-24, docs/plan-ollama-migration.md Phase 4). LM
Studio is permanently unavailable (2026-07-24, user decision) -- this script
now targets `sim-smart` on the sim server's own Ollama instance
(http://localhost:11434/api/chat, OLLAMA_CHAT_URL in simulation/server.py).

Replays requests recorded in a session's llm.jsonl (falls back to the
pre-rename lm_studio.jsonl for old, LM-Studio-era sessions) so payload/config
changes get a before/after number instead of ad-hoc eyeballing (the
2026-07-05 qwen-vs-gemma comparison in server.py:41-46 had no repeatable
harness). Logged sessions from before the Ollama cutover have LM
Studio-shaped requests (`reasoning_effort`, no `think`/`format` keys) -- this
script extracts the portable bits (messages, max_tokens, temperature,
response_format, reasoning_effort) and rebuilds a native Ollama request body
via simulation/llm_wire.to_ollama_body(); it does NOT repost the old payload
verbatim (Ollama's OpenAI-compat endpoint silently ignores think:false --
see ollama_config.md -- so that endpoint is never used here).

Modes:
  --as-logged   translate the logged request as directly as possible: pass
                through max_tokens/temperature/response_format/sampling keys
                unchanged, and translate the logged reasoning_effort=="none"
                marker (LM Studio's thinking-suppression knob) to Ollama's
                think:false 1:1 -- i.e. replay whatever thinking policy was
                actually in effect when the call was logged, on the new
                runtime.
  --patched     ignore the logged reasoning_effort/sampling and apply the
                CURRENT production transform from simulation/server.py's
                build_decision_payload: routine turns get NON_THINKING_SAMPLING
                + think:false; invention/high-stakes turns get
                THINKING_SAMPLING and no think key (model may think).

Intentionally standalone re: server.py's Flask app / SessionLogger / SimEngine
(none of that is imported -- see simulation/prompts.py's module docstring for
why importing server.py itself is unsafe from a script). It DOES import the
small, pure simulation/llm_wire.to_ollama_body() helper -- the same wire-format
conversion server.py uses at its POST call sites -- so this bench measures the
real conversion, not a re-implementation that could drift.

Usage:
  uv run python scripts/llm_replay_bench.py --as-logged            # latest session
  uv run python scripts/llm_replay_bench.py --patched --n 40
  uv run python scripts/llm_replay_bench.py --as-logged --session simulation/logs/2026-07-23T23-55-58

Pause the sim server first (POST /control/pause) or its own think traffic
will contend for sim-smart's OLLAMA_NUM_PARALLEL slots and skew latencies.
"""

import argparse
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
SIMULATION_DIR = os.path.join(BASE_DIR, "simulation")
sys.path.insert(0, SIMULATION_DIR)
import llm_wire  # noqa: E402  -- shared to_ollama_body conversion (see module docstring)

OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
MODEL_SMART = "sim-smart"  # bench always targets the decision model

# Qwen-recommended sampling pins (see ollama_config.md / Qwen model card;
# historical rationale carried from the former LM Studio runtime's
# lms_config.md, now removed). Mirrors NON_THINKING_SAMPLING/THINKING_SAMPLING
# in simulation/server.py.
NON_THINKING_SAMPLING = {"top_p": 0.8, "top_k": 20, "min_p": 0}
THINKING_SAMPLING = {"top_p": 0.95, "top_k": 20}


def _llm_log_path(session_dir):
    """llm.jsonl is the current stream name (Phase 5); lm_studio.jsonl is the
    fallback for replaying OLD sessions logged before the rename."""
    path = os.path.join(session_dir, "llm.jsonl")
    if os.path.isfile(path):
        return path
    return os.path.join(session_dir, "lm_studio.jsonl")


def latest_session_dir():
    dirs = [d for d in os.listdir(LOGS_DIR)
            if os.path.isdir(os.path.join(LOGS_DIR, d))
            and (os.path.isfile(os.path.join(LOGS_DIR, d, "llm.jsonl"))
                 or os.path.isfile(os.path.join(LOGS_DIR, d, "lm_studio.jsonl")))]
    if not dirs:
        return None
    return os.path.join(LOGS_DIR, sorted(dirs)[-1])


def load_entries(session_dir, n):
    """Logged decision calls with a full request payload, oldest first."""
    entries = []
    path = _llm_log_path(session_dir)
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


def request_to_internal_payload(req):
    """Extract the portable fields from a logged (possibly LM-Studio-shaped)
    request dict into this repo's internal OpenAI-chat-shaped payload, always
    targeting MODEL_SMART (the bench replays decision calls only). The
    logged `reasoning_effort: "none"` marker -- LM Studio's thinking-
    suppression knob -- is translated 1:1 to Ollama's think:false so
    --as-logged reproduces the thinking policy that was actually in effect
    when the call was logged."""
    payload = {"model": MODEL_SMART, "messages": req.get("messages")}
    for key in ("max_tokens", "temperature", "response_format",
                "top_p", "top_k", "min_p", "presence_penalty"):
        if key in req:
            payload[key] = req[key]
    if req.get("reasoning_effort") == "none":
        payload["think"] = False
    return payload


def patch_payload(payload, invention_only):
    """The current-production transform (mirrors
    simulation/server.py:build_decision_payload): routine turns get
    think:false + NON_THINKING_SAMPLING; invention/high-stakes turns keep
    thinking (no think key) + THINKING_SAMPLING."""
    p = dict(payload)
    p.pop("think", None)
    p.pop("reasoning_effort", None)
    for k in ("top_p", "top_k", "min_p"):
        p.pop(k, None)
    if invention_only:
        p.update(THINKING_SAMPLING)
        return p
    p.update(NON_THINKING_SAMPLING)
    p["think"] = False
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
    internal = request_to_internal_payload(entry["request"])
    invention_only = bool(entry.get("invention_only"))
    if mode == "patched":
        internal = patch_payload(internal, invention_only)
    body = llm_wire.to_ollama_body(internal)
    t0 = time.perf_counter()
    try:
        resp = requests.post(OLLAMA_CHAT_URL, json=body, timeout=timeout)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        resp_body = resp.json()
    except Exception as exc:  # timeout / connection / bad JSON
        return {
            "agent": entry.get("agent_name"),
            "invention_only": invention_only,
            "error": f"{type(exc).__name__}: {exc}",
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        }
    if resp.status_code != 200:
        err = resp_body.get("error") if isinstance(resp_body, dict) else None
        err_text = str(err.get("message") if isinstance(err, dict) else err or "")
        return {
            "agent": entry.get("agent_name"),
            "invention_only": invention_only,
            "error": f"http {resp.status_code}: {err_text}",
            "latency_ms": latency_ms,
        }
    message = resp_body.get("message") or {}
    content = (message.get("content") or "").strip()
    thinking_field = (message.get("thinking") or "").strip()
    decision = extract_decision(content)
    think_leak = "<think>" in content.lower()
    return {
        "agent": entry.get("agent_name"),
        "invention_only": invention_only,
        "error": None,
        "latency_ms": latency_ms,
        "done_reason": resp_body.get("done_reason"),
        "thinking_leak": think_leak,
        "thinking_field_present": bool(thinking_field),
        "prompt_eval_count": resp_body.get("prompt_eval_count"),
        "eval_count": resp_body.get("eval_count"),
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
    lines.append(f"done_reason=length: {pct(sum(r['done_reason'] == 'length' for r in ok), len(ok))}")
    lines.append(f"thinking leak (<think> in content): "
                 f"{pct(sum(r['thinking_leak'] for r in routine), len(routine))} of routine")
    ec = [r["eval_count"] for r in routine if r.get("eval_count") is not None]
    if ec:
        lines.append(f"routine eval_count (completion tokens): mean {statistics.mean(ec):.0f}")
    pec = [r["prompt_eval_count"] for r in ok if r.get("prompt_eval_count") is not None]
    if pec:
        lines.append(f"prompt_eval_count: mean {statistics.mean(pec):.0f}  max {max(pec)}")
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
                    help="concurrent requests; match OLLAMA_NUM_PARALLEL slots (default 2)")
    ap.add_argument("--timeout", type=int, default=120, help="per-request timeout s")
    ap.add_argument("--out", help="report JSONL path (default simulation/logs/replay_bench/)")
    args = ap.parse_args()

    try:
        tags = requests.get(OLLAMA_TAGS_URL, timeout=5).json()
        ids = [m.get("name") for m in tags.get("models", [])]
    except Exception as exc:
        print(f"Ollama is not reachable at {OLLAMA_TAGS_URL} ({exc}); start it and retry.")
        return 2
    if not any((MODEL_SMART in (i or "")) for i in ids):
        print(f"WARNING: {MODEL_SMART} not found in `ollama list` ({ids}); requests will 404.")
    print(f"Ollama up; models: {ids}")

    session_dir = args.session or latest_session_dir()
    if not session_dir or not os.path.isdir(session_dir):
        print(f"No session dir found ({session_dir!r}).")
        return 2
    entries = load_entries(session_dir, args.n)
    if not entries:
        print(f"No replayable decision entries in {session_dir}.")
        return 2
    print(f"Replaying {len(entries)} logged calls from {session_dir} "
          f"[mode={args.mode}, workers={args.workers}] against {MODEL_SMART}")

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
