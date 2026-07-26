"""Run the deterministic Daily Council integration matrix.

Each cell runs in a fresh subprocess with a temporary ``state.db`` path. The
feature flag is overridden in memory (and, for the Daily Council smoke, echoed
through ``DAILY_COUNCIL_TEST_FLAG``); no source file or real save is modified.

Run:
    uv run python scripts/daily_council_regression.py

Writes:
    scripts/out/daily_council_regression.json
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "scripts" / "out" / "daily_council_regression.json"
SMOKES = (
    "scripts/daily_council_smoke.py",
    "scripts/sid_parity_smoke.py",
    "scripts/path1_smoke.py",
)
FLAGS = (True, False)

BOOTSTRAP = r"""
import os
import runpy
import sys
from pathlib import Path

root = Path(sys.argv[1])
script = root / sys.argv[2]
enabled = sys.argv[3] == "true"
db_path = sys.argv[4]
sys.path.insert(0, str(root / "simulation"))

import sim_engine as se

se.DB_PATH = db_path
se.DAILY_COUNCIL_ENABLED = enabled
os.environ["DAILY_COUNCIL_TEST_FLAG"] = "true" if enabled else "false"
runpy.run_path(str(script), run_name="__main__")
"""


def _tail(text: str, limit: int = 4000) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[-limit:]


def run_cell(script: str, enabled: bool) -> dict:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="daily-council-regression-") as tmp:
        db_path = str(Path(tmp) / "state.db")
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                BOOTSTRAP,
                str(ROOT),
                script,
                "true" if enabled else "false",
                db_path,
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    runtime = time.perf_counter() - started
    return {
        "smoke": script,
        "daily_council_enabled": enabled,
        "pass": completed.returncode == 0,
        "returncode": completed.returncode,
        "runtime_seconds": round(runtime, 3),
        "fixture": "isolated temporary state.db (deleted after subprocess)",
        "stdout_tail": _tail(completed.stdout),
        "stderr_tail": _tail(completed.stderr),
    }


def main() -> int:
    started = time.perf_counter()
    cells = [
        run_cell(script, enabled)
        for enabled in FLAGS
        for script in SMOKES
    ]
    by_flag = {
        ("on" if enabled else "off"): {
            Path(cell["smoke"]).name: cell["pass"]
            for cell in cells
            if cell["daily_council_enabled"] is enabled
        }
        for enabled in FLAGS
    }
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pass": all(cell["pass"] for cell in cells),
        "runtime_seconds": round(time.perf_counter() - started, 3),
        "matrix": by_flag,
        "cells": cells,
        "isolation": {
            "subprocess_per_cell": True,
            "source_files_mutated": False,
            "real_state_db_touched": False,
        },
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "pass": result["pass"],
        "runtime_seconds": result["runtime_seconds"],
        "matrix": result["matrix"],
        "artifact": str(OUT_PATH),
    }, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
