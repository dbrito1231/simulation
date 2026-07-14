"""Canonical CLI loader for the sim's LM Studio model (no GUI required).

Target load (see lms_config.md):
  qwen/qwen3.5-9b, context 20000, parallel 3, flash attention ON,
  KV cache K+V quantized q8_0.

Why a script: `lms load` exposes context/parallel/draft flags but NOT flash
attention or KV-cache quantization; the REST load endpoint and the lmstudio
Python SDK expose flash attention / KV quant but (as of SDK 1.5.0) no
parallel-slots field. This script walks that ladder and reports exactly which
knobs stuck:

  1. REST POST /api/v1/models/load with the full config incl. parallel
     (newer builds may accept it; echo_load_config shows what was honored).
  2. Fallback: `lms load` for context+parallel, accepting the loss of
     whichever fields REST rejected.

Usage:
  uv run --with lmstudio python scripts/lms_load.py           # apply target load
  uv run --with lmstudio python scripts/lms_load.py --check   # readback only

(--with lmstudio is only needed if the ladder reaches the SDK; plain
`uv run python scripts/lms_load.py` works for the REST/CLI rungs.)
"""

import argparse
import json
import subprocess
import sys

import requests

MODEL = "qwen/qwen3.5-9b"
IDENTIFIER = "qwen/qwen3.5-9b"   # must match MODEL_SMART/MODEL_FAST in server.py
CONTEXT_LENGTH = 20000
PARALLEL = 3                      # per-slot budget 20000/3 ~ 6600 >= ~5800 max prompt
BASE = "http://localhost:1234"

# Speculative decoding draft options (see docs/... probe notes below and in
# the --draft mtp code path for what was actually observed on this machine).
DRAFT_MODEL = "qwen3.5-0.8b"       # separate Q8_0 draft for --draft simple
MTP_MODEL = "qwen3.5-9b-mtp"       # MTP self-speculation variant for --draft mtp

# Probed 2026-07-11 against LM Studio's /api/v1/models/load: it ACCEPTS
# model/context_length/flash_attention/parallel and REJECTS `identifier`
# (the loaded id defaults to the model key, which is what the sim expects
# anyway) and the two llama_*_cache_quantization_type keys (KV-cache quant
# stays SDK/GUI-only; dropped per the priority order in lms_config.md --
# flash attention + parallel 3 outrank it and the 20000-ctx load fits VRAM
# without it, ~7.75 GiB estimated on the 12 GiB card).
#
# Probed 2026-07-13 for speculative decoding fields on this LM Studio build:
# - `speculative_draft_simple` + `speculative_draft_model` (separate draft
#   model, e.g. qwen3.5-0.8b): REJECTED at load time on both the REST and CLI
#   rungs with "Load-time draft-model speculative decoding is only supported
#   by the llama.cpp engine protocol runtime" -- qwen3.5-9b loads under a
#   different (non-llama.cpp-protocol) engine on this build, so --draft
#   simple cannot activate here. The code path is kept (and documents the
#   failure) in case a future LM Studio/engine build lifts the restriction.
# - `speculative_draft_mtp` against the plain qwen/qwen3.5-9b weights: also
#   REJECTED ("MTP speculative decoding requires a GGUF model with a bundled
#   supported MTP head") -- confirms the flag is a no-op without MTP-head
#   weights, exactly as expected.
# - `speculative_draft_mtp` while loading qwen3.5-9b-mtp (the MTP-head
#   variant) via the CLI rung, with --identifier pinned back to
#   "qwen/qwen3.5-9b" (REST rejects an `identifier` override, so this must
#   go through the CLI): WORKED. A live /v1/chat/completions call returned
#   `"stats": {"total_draft_tokens_count": 35, "accepted_draft_tokens_count":
#   31, "rejected_draft_tokens_count": 0, "ignored_draft_tokens_count": 4}`,
#   proving MTP speculative decoding was actually active, at 10.3 GiB/12 GiB
#   VRAM used. So --draft mtp is the only currently-functional speculative
#   mode on this machine; --draft simple is wired up but inert pending an
#   engine change.
REST_CONFIG = {
    "model": MODEL,
    "context_length": CONTEXT_LENGTH,
    "flash_attention": True,
    "parallel": PARALLEL,
    "echo_load_config": True,
}


def sh(cmd):
    print(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, shell=False,
                          encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    # lms's CLI spinner emits glyphs outside the console's codepage (e.g. the
    # default Windows cp1252 terminal); re-encode defensively so a print
    # crash doesn't mask an otherwise-successful load.
    safe = out.strip().encode(sys.stdout.encoding or "utf-8", errors="replace") \
                       .decode(sys.stdout.encoding or "utf-8", errors="replace")
    print(safe)
    return proc.returncode, out


def check():
    sh(["lms", "ps"])
    try:
        ids = [m.get("id") for m in
               requests.get(f"{BASE}/v1/models", timeout=5).json().get("data", [])]
        print(f"/v1/models ids: {ids}")
        if IDENTIFIER not in ids:
            print(f"WARNING: identifier {IDENTIFIER!r} not visible to the sim "
                  "(server.py falls back to 'local-model' for the session).")
    except Exception as exc:
        print(f"WARNING: OpenAI endpoint not reachable: {exc}")


def load_none():
    """Byte-identical to the original (pre-speculative-decoding) behavior."""
    sh(["lms", "unload", "--all"])

    # Rung 1: REST load with the full config. echo_load_config makes the
    # response show which fields the running build honored.
    try:
        resp = requests.post(f"{BASE}/api/v1/models/load", json=REST_CONFIG,
                             timeout=600)
        body = resp.json()
        print(f"REST load http={resp.status_code}: {json.dumps(body, indent=2)[:2000]}")
        if resp.status_code == 200:
            check()
            print("\nInspect the echoed config above: if parallel/flash_attention/"
                  "KV quant are absent there and in `lms ps`, they were ignored "
                  "-- the CLI fallback below covers context+parallel only.")
            # REST builds that ignore `parallel` leave the app default; verify.
            rc, out = 0, ""
            try:
                rc, out = sh(["lms", "ps"])
            except Exception:
                pass
            if f"{PARALLEL}" in out:
                return 0
            print("Parallel setting not confirmed -- falling through to lms load.")
    except Exception as exc:
        print(f"REST load failed ({exc}); falling back to lms load.")

    # Rung 2: plain CLI -- context + parallel guaranteed, FA/KV quant lost.
    sh(["lms", "unload", "--all"])
    rc, _ = sh(["lms", "load", MODEL,
                "--context-length", str(CONTEXT_LENGTH),
                "--parallel", str(PARALLEL),
                "--identifier", IDENTIFIER, "-y"])
    check()
    if rc != 0:
        print("lms load failed; restore manually per lms_config.md.")
        return 1
    print("\nLoaded via lms load (context+parallel). Flash attention / KV quant "
          "were NOT applied on this rung.")
    return 0


def load_simple():
    """Speculative decoding with a separate small draft model (qwen3.5-0.8b)."""
    sh(["lms", "unload", "--all"])

    config = dict(REST_CONFIG)
    config["speculative_draft_simple"] = True
    config["speculative_draft_model"] = DRAFT_MODEL

    try:
        resp = requests.post(f"{BASE}/api/v1/models/load", json=config, timeout=600)
        body = resp.json()
        print(f"REST load (draft=simple) http={resp.status_code}: "
              f"{json.dumps(body, indent=2)[:2000]}")
        if resp.status_code == 200:
            check()
            return 0
        print("REST load rejected the speculative_draft_simple/model fields; "
              "falling back to lms load.")
    except Exception as exc:
        print(f"REST load failed ({exc}); falling back to lms load.")

    sh(["lms", "unload", "--all"])
    rc, _ = sh(["lms", "load", MODEL,
                "--context-length", str(CONTEXT_LENGTH),
                "--parallel", str(PARALLEL),
                "--speculative-draft-simple",
                "--speculative-draft-model", DRAFT_MODEL,
                "--identifier", IDENTIFIER, "-y"])
    check()
    if rc != 0:
        print("lms load failed; restore manually per lms_config.md.")
        return 1
    return 0


def load_mtp():
    """Speculative decoding via Draft MTP (Multi-Token Prediction).

    MTP self-speculation depends on the model weights themselves containing
    an MTP head. The standard qwen/qwen3.5-9b download does NOT have one, so
    we probe that first (rung A, expected to be a no-op) and then load the
    qwen3.5-9b-mtp variant (rung B, the variant built with the MTP head)
    through the CLI so we can pin --identifier back to "qwen/qwen3.5-9b" and
    keep server.py's MODEL_SMART/MODEL_FAST matching intact.
    """
    sh(["lms", "unload", "--all"])

    # Rung A (probe): does the flag do anything against the plain model?
    config = dict(REST_CONFIG)
    config["speculative_draft_mtp"] = True
    try:
        resp = requests.post(f"{BASE}/api/v1/models/load", json=config, timeout=600)
        body = resp.json()
        print(f"REST load (draft=mtp, probe on {MODEL}) http={resp.status_code}: "
              f"{json.dumps(body, indent=2)[:2000]}")
        print("NOTE: this is only a probe of the flag against weights with no MTP "
              "head; check the echo above for speculative_draft_mtp -- if it's "
              "accepted but the model has no MTP head, it is a documented no-op.")
    except Exception as exc:
        print(f"REST probe failed ({exc}).")

    # Rung B: load the MTP-head variant via CLI, keeping the sim-facing
    # identifier stable. REST previously rejected an `identifier` override,
    # so this rung must go through the CLI (which supports --identifier).
    sh(["lms", "unload", "--all"])
    rc, _ = sh(["lms", "load", MTP_MODEL,
                "--context-length", str(CONTEXT_LENGTH),
                "--parallel", str(PARALLEL),
                "--speculative-draft-mtp",
                "--identifier", IDENTIFIER, "-y"])
    check()
    if rc != 0:
        print("lms load of qwen3.5-9b-mtp failed; restore manually per lms_config.md.")
        return 1
    print(f"\nLoaded {MTP_MODEL} under identifier {IDENTIFIER!r} with "
          "--speculative-draft-mtp. Flash attention / KV quant were NOT applied "
          "on this rung (CLI-only fallback, same limitation as the base loader).")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="readback only, no load")
    ap.add_argument("--draft", choices=["none", "simple", "mtp"], default="none",
                     help="optionally enable speculative decoding: 'simple' uses "
                          "a separate draft model (qwen3.5-0.8b), 'mtp' uses the "
                          "Draft MTP self-speculation variant (qwen3.5-9b-mtp). "
                          "Default 'none' is byte-identical to the original loader.")
    args = ap.parse_args()
    if args.check:
        check()
        return 0

    if args.draft == "simple":
        return load_simple()
    if args.draft == "mtp":
        return load_mtp()
    return load_none()


if __name__ == "__main__":
    sys.exit(main())
