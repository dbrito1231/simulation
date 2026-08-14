"""Spectator prediction market persistence (idea-04).

Append-on-write / resolve-on-read store backed by simulation/predictions.json.
Thread-safe for Flask's threaded dev server. Mirrors memory_store.py's
absent/corrupt-tolerant load and atomic tmp + os.replace() persist pattern.
"""

import json
import os
import threading

VALID_BALLOT_KINDS = frozenset({"rule", "blueprint", "idea", "succession"})


class PredictionsStore:
    """Pending and resolved spectator predictions against Daily Council ballots."""

    def __init__(self, path):
        self.path = path
        self.predictions = []
        self._next_id = 1
        self._lock = threading.Lock()
        self._load_locked_startup()

    def _load_locked_startup(self):
        if not os.path.exists(self.path):
            self._load_status = ("absent", 0)
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            rows = data.get("predictions") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                rows = []
            rebuilt = []
            max_id = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                pid = row.get("id")
                if pid is None:
                    continue
                pid_str = str(pid)
                try:
                    max_id = max(max_id, int(pid_str))
                except (TypeError, ValueError):
                    pass
                rebuilt.append({
                    "id": pid_str,
                    "kind": row.get("kind"),
                    "question": row.get("question"),
                    "pick": row.get("pick"),
                    "ballot_frame_tick": row.get("ballot_frame_tick"),
                    "correct": row.get("correct"),
                    "verdict": row.get("verdict"),
                    "resolved_frame_tick": row.get("resolved_frame_tick"),
                })
            with self._lock:
                self.predictions = rebuilt
                self._next_id = max_id + 1
            self._load_status = ("loaded", len(rebuilt))
        except (OSError, ValueError, TypeError, AttributeError):
            with self._lock:
                self.predictions = []
                self._next_id = 1
            self._load_status = ("corrupt", 0)

    def _persist(self):
        with self._lock:
            predictions_copy = list(self.predictions)
        payload = {"predictions": predictions_copy}
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            pass

    @staticmethod
    def _valid_submit(kind, question, pick, ballot_frame_tick):
        if kind not in VALID_BALLOT_KINDS:
            return False
        if not isinstance(question, str) or not question.strip():
            return False
        if not isinstance(pick, str) or not pick.strip():
            return False
        if not isinstance(ballot_frame_tick, int):
            return False
        return True

    def submit(self, kind, question, pick, ballot_frame_tick):
        if not self._valid_submit(kind, question, pick, ballot_frame_tick):
            return None
        with self._lock:
            pid = str(self._next_id)
            self._next_id += 1
            record = {
                "id": pid,
                "kind": kind,
                "question": question.strip(),
                "pick": pick.strip(),
                "ballot_frame_tick": ballot_frame_tick,
                "correct": None,
                "verdict": None,
                "resolved_frame_tick": None,
            }
            self.predictions.append(record)
        self._persist()
        return pid

    def resolve(self, prediction_id, correct, verdict):
        if prediction_id is None:
            return False
        pid = str(prediction_id)
        if not isinstance(correct, bool):
            return False
        if not isinstance(verdict, str) or not verdict.strip():
            return False
        with self._lock:
            target = None
            for row in self.predictions:
                if row.get("id") == pid:
                    target = row
                    break
            if target is None:
                return False
            if target.get("correct") is not None:
                return False
            target["correct"] = correct
            target["verdict"] = verdict.strip()
            target["resolved_frame_tick"] = None
        self._persist()
        return True

    def history(self):
        with self._lock:
            rows = list(self.predictions)
        resolved = [r for r in rows if isinstance(r.get("correct"), bool)]
        total = len(resolved)
        if total == 0:
            hit_rate = None
        else:
            correct_count = sum(1 for r in resolved if r.get("correct") is True)
            hit_rate = correct_count / total
        return {"predictions": rows, "hitRate": hit_rate}
