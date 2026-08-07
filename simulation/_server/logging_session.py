"""SessionLogger (append-only JSON Lines per-session logging), split out of
server.py (Phase 5 modularization, pure move, no behavior change).

server.py still owns the singleton (`session_logger = SessionLogger(...)`)
and its atexit registration -- that's bootstrap/initialization, not pure
helper logic, so it stays in the entry-point module. This file has the class
definition plus the constants/helper it needs.
"""

import json
import os
import re
import shutil
import threading
from datetime import datetime, timezone

# Session-log retention (docs/plan-log-retention.md): keep-N-newest, pruned
# once at SessionLogger.__init__ right after the current session's directory
# is created. Only directories whose basename fully matches this regex are
# ever candidates -- loose files in logs/ root (soak-*.json, path1_soak_*,
# *.db) and non-session subdirs (replay_bench/) are never touched.
SESSION_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}$")
# SIM_LOG_RETENTION env override, parsed defensively -- a missing/blank/
# malformed value falls back to the 20 default rather than raising at import.
try:
    LOG_RETENTION_SESSIONS = int(os.environ.get("SIM_LOG_RETENTION", "20") or 20)
except (TypeError, ValueError):
    LOG_RETENTION_SESSIONS = 20
# Buffered benchmarks.jsonl writes: _sample_benchmarks emits many records per
# burst; cap prevents unbounded memory if flush is delayed.
BENCHMARK_BUFFER_MAX = 256
# SIM_LLM_LOG_FULL: when true, llm.jsonl records include full request/response
# bodies (legacy default). Default off — slim records omit them to cut disk I/O.
LLM_LOG_FULL = str(os.environ.get("SIM_LLM_LOG_FULL", "")).strip().lower() in (
    "1", "true", "yes", "on",
)
_LLM_RESPONSE_PREVIEW_MAX = 240


def _llm_response_preview(response):
    """Short excerpt from an Ollama response body for slim llm.jsonl records."""
    if response is None:
        return None
    text = None
    if isinstance(response, dict):
        msg = response.get("message")
        if isinstance(msg, dict):
            text = msg.get("content") or msg.get("reasoning_content")
        if text is None and response.get("error"):
            text = str(response.get("error"))
    elif isinstance(response, str):
        text = response
    if not text:
        return None
    text = str(text).strip()
    if len(text) <= _LLM_RESPONSE_PREVIEW_MAX:
        return text
    return text[:_LLM_RESPONSE_PREVIEW_MAX] + "…"


class SessionLogger:
    """Append-only JSON Lines logger. One session folder per server run."""

    def __init__(self, base_dir):
        self.session_id = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        self.dir = os.path.join(base_dir, "logs", self.session_id)
        os.makedirs(self.dir, exist_ok=True)
        self._prune_old_sessions(os.path.join(base_dir, "logs"))
        self.activity_path = os.path.join(self.dir, "activity.jsonl")
        self.conversation_path = os.path.join(self.dir, "conversation.jsonl")
        self.llm_path = os.path.join(self.dir, "llm.jsonl")
        # benchmarks.jsonl (Phase 0/8): a dedicated metrics stream so Sid-like
        # features can be measured (specialization index, rule adherence,
        # meme adoption, memory-store size, module-activation timeline).
        self.benchmark_path = os.path.join(self.dir, "benchmarks.jsonl")
        # divine.jsonl (Sovereign God mode Phase 2, docs/plan-sovereign-god-
        # mode-v2.md's "Logging" section): the fifth stream, one record per
        # applied/cancelled/expired/rejected-after-preview/restore-closed
        # divine intervention. Preview-only calls are not world events and
        # never reach this stream. Never receives the token or raw request
        # headers -- see log_divine below, which only accepts an already-
        # hashed request_id.
        self.divine_path = os.path.join(self.dir, "divine.jsonl")
        # compiler.jsonl (Sovereign God mode Optional Phase 8, docs/plan-
        # sovereign-god-mode-v2.md "Log separately"): a SIXTH stream, one
        # record per free-prose compile attempt (draft or rejection). Kept
        # separate from llm.jsonl (agent cognition) and divine.jsonl
        # (world-affecting audit) on purpose -- a compile is neither. Never
        # receives SIM_GOD_TOKEN -- see log_compiler below.
        self.compiler_path = os.path.join(self.dir, "compiler.jsonl")
        self._benchmark_buffer = []
        self._benchmark_lock = threading.Lock()
        for path in [self.activity_path, self.conversation_path, self.llm_path,
                     self.benchmark_path, self.divine_path, self.compiler_path]:
            open(path, "a", encoding="utf-8").close()
        self.log_conversation(
            "system",
            "log",
            "Conversation log started. Agent speech, directives, and talk attempts are recorded here.",
            kind="session_start",
        )

    def _prune_old_sessions(self, logs_root):
        """Keep the LOG_RETENTION_SESSIONS newest session directories under
        logs_root, deleting the rest. Runs once, right after this session's
        own directory is created (no thread/tick). Never raises -- mirrors
        _append's "logging must never break the simulation" contract: a
        listing failure aborts pruning for this run, and a per-directory
        deletion failure is swallowed so one un-deletable folder never blocks
        the rest. docs/plan-log-retention.md / specs/12-ops.md."""
        keep = LOG_RETENTION_SESSIONS
        if keep <= 0:
            return  # retention disabled -- keep everything
        try:
            names = sorted(
                name for name in os.listdir(logs_root)
                if SESSION_DIR_RE.match(name)
                and os.path.isdir(os.path.join(logs_root, name))
            )  # session-id names are ISO %Y-%m-%dT%H-%M-%S: lexicographic
               # sort == chronological sort, no stat() needed
        except OSError:
            return
        stale = [name for name in names[:-keep] if name != self.session_id]
        for name in stale:
            try:
                shutil.rmtree(os.path.join(logs_root, name))
            except OSError:
                pass  # best-effort; one un-deletable dir must not block others

    def _append(self, path, record):
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": self.session_id,
            **record,
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            # Logging must never break the simulation.
            pass

    def log_activity(self, message, frame_tick=None):
        self._append(self.activity_path, {
            "type": "activity", "message": message, "frame_tick": frame_tick,
        })

    def log_conversation(self, sender, recipient, message, frame_tick=None,
                         kind="speech", outcome=None):
        record = {
            "type": "conversation",
            "kind": kind,
            "from": sender,
            "to": recipient,
            "message": message,
            "frame_tick": frame_tick,
        }
        if outcome:
            record["outcome"] = outcome
        self._append(self.conversation_path, record)

    def log_lm_exchange(self, record):
        record = dict(record)
        if not LLM_LOG_FULL:
            record.pop("request", None)
            response = record.pop("response", None)
            preview = _llm_response_preview(response)
            if preview is not None:
                record["response_preview"] = preview
        record = {"type": "llm", **record}
        self._append(self.llm_path, record)

    def _stamp_record(self, record):
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": self.session_id,
            **record,
        }

    def log_benchmark(self, metric, value, frame_tick=None, detail=None):
        record = {
            "type": "benchmark",
            "metric": metric,
            "value": value,
            "frame_tick": frame_tick,
        }
        if detail is not None:
            record["detail"] = detail
        with self._benchmark_lock:
            self._benchmark_buffer.append(record)
            if len(self._benchmark_buffer) >= BENCHMARK_BUFFER_MAX:
                self._flush_benchmark_buffer_unlocked()

    def flush_benchmarks(self):
        """Write all buffered benchmark records in one file append."""
        with self._benchmark_lock:
            self._flush_benchmark_buffer_unlocked()

    def _flush_benchmark_buffer_unlocked(self):
        if not self._benchmark_buffer:
            return
        lines = [
            json.dumps(self._stamp_record(record), ensure_ascii=False) + "\n"
            for record in self._benchmark_buffer
        ]
        self._benchmark_buffer.clear()
        try:
            with open(self.benchmark_path, "a", encoding="utf-8") as fh:
                fh.write("".join(lines))
        except OSError:
            pass

    def log_divine(self, intervention_id=None, request_id=None, frame_tick=None,
                   kind=None, normalized_command=None, outcome=None,
                   status=None, public=None):
        """Sovereign God mode Phase 2. `request_id` must already be hashed by
        the caller (sim_engine._hash_request_id) -- this method never sees
        (and therefore can never log) the God token or any raw HTTP header."""
        record = {
            "type": "divine",
            "intervention_id": intervention_id,
            "request_id": request_id,
            "frame_tick": frame_tick,
            "kind": kind,
            "normalized_command": normalized_command,
            "outcome": outcome,
            "status": status,
            "public": public,
        }
        self._append(self.divine_path, record)

    def log_compiler(self, prose=None, model=None, latency_ms=None,
                     status=None, reason=None, preview_id=None):
        """Sovereign God mode Optional Phase 8. `prose` is the operator's
        already-normalized free-text input, `status` is "draft" or
        "rejected", `reason` is set only for rejections. Never accepts or
        logs SIM_GOD_TOKEN -- sim_engine.god_compile_prose never sees the
        token in the first place, so there is nothing to redact here."""
        record = {
            "type": "compiler",
            "prose": prose,
            "model": model,
            "latency_ms": latency_ms,
            "status": status,
            "reason": reason,
            "preview_id": preview_id,
        }
        self._append(self.compiler_path, record)
