"""One-shot ops: prune ruined structures from simulation/state.db."""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simulation.sim_engine import DB_PATH, _read_state_db, _write_state_db

BACKUP_DIR = ROOT / "simulation" / "backup"


def _backup_db(timestamp: str) -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        src = Path(DB_PATH + suffix)
        if not src.exists():
            continue
        dest = BACKUP_DIR / f"state.db.pre-ruin-prune-{timestamp}.bak{suffix}"
        shutil.copy2(src, dest)
        print(f"Backed up {src.name} -> {dest.name}")


def main() -> int:
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    _backup_db(timestamp)

    payload = _read_state_db(DB_PATH)
    if payload is None or not isinstance(payload, dict):
        print(f"Error: could not read valid state from {DB_PATH}", file=sys.stderr)
        return 1

    civ = payload.get("civilization")
    if not isinstance(civ, dict):
        print("Error: payload missing civilization dict", file=sys.stderr)
        return 1

    structures = civ.get("structures")
    if not isinstance(structures, list):
        print("Error: civilization.structures is not a list", file=sys.stderr)
        return 1

    total = len(structures)
    ruin_ids = {s["id"] for s in structures if s.get("isRuin")}
    ruined = len(ruin_ids)
    kept = total - ruined

    print(f"Before: total={total} ruined={ruined} kept={kept}")

    civ["structures"] = [s for s in structures if not s.get("isRuin")]

    for agent in payload.get("agents") or []:
        if agent.get("homeStructureId") in ruin_ids:
            agent["homeStructureId"] = None

    reorg_tasks = civ.get("reorgTasks")
    if reorg_tasks is not None:
        civ["reorgTasks"] = [
            t for t in reorg_tasks if t.get("structureId") not in ruin_ids
        ]

    _write_state_db(DB_PATH, payload)

    after_total = len(civ["structures"])
    print(f"After: total={after_total} ruined=0 kept={after_total}")
    print(f"Wrote pruned state to {DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
