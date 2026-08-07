"""In-process vector memory store, split out of server.py (Phase 5
modularization, pure move, no behavior change).

Phase 1: in-process vector memory store (replaces ChromaDB/Docker). CMA's
shared vector store + Sid's WM/STM/LTM tiers, kept in-process to honor the
no-external-service ethos. Embedding is a deterministic hashing trick
(bag-of-tokens hashed into a fixed dimension, L2-normalized) so cosine
similarity == dot product. Swappable for a real embedding model / Chroma
later behind the identical /memory/* endpoints (still in server.py).

server.py still owns the singleton (`memory_store = MemoryStore(...)`) since
its mirror_path depends on session_logger.dir (a server.py bootstrap value) --
that's initialization, not pure helper logic, so it stays in the entry-point
module. This file has the class definition plus the constants/helpers it and
its callers need.
"""

import hashlib
import json
import math
import os
import re
import threading
from datetime import datetime, timezone

MEMORY_DIM = 128
MEMORY_MAX_ENTRIES = 1200       # global cap; the cleaner trims past this
MEMORY_PERSIST_EVERY = 12       # debounce: rewrite memory.json every N stores
_MEMORY_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Tokens that carry no salience signal, dropped before embedding.
_MEMORY_STOPWORDS = frozenset(
    "the a an and or to of for in on at is are was were be been has have had "
    "i you he she it we they me him her them my your his its our their this "
    "that with from into nothing none".split()
)


def _stable_hash(token):
    """Process-stable hash so persisted vectors survive a reload."""
    return int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)


def embed_text(text):
    """Hashing-trick embedding: L2-normalized bag-of-tokens vector."""
    vec = [0.0] * MEMORY_DIM
    if not text:
        return vec
    for tok in _MEMORY_TOKEN_RE.findall(text.lower()):
        if tok in _MEMORY_STOPWORDS:
            continue
        vec[_stable_hash(tok) % MEMORY_DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _cosine(a, b):
    return sum(x * y for x, y in zip(a, b))


# Reasoning models (e.g. qwen3.5) sometimes route their entire output --
# chain-of-thought scaffold included -- into `reasoning_content` instead of
# `content`. Left unchecked, that scaffold gets stored verbatim as agent
# memory and re-enters every future prompt via compose_memory() (prompt_
# format.py). These two helpers extract the real answer and reject anything
# that still looks like leaked scaffolding, for both the plain-text LLM path
# (server.lm_complete) and the memory stores that may already hold poisoned
# entries (MemoryStore.clean, and the engine's longTerm lists -- see
# server._ENGINE_DEPS).
_SCAFFOLD_MARKER_RE = re.compile(
    r"(thinking process|\*\*analyze|let'?s think|let me think|"
    r"chain[- ]of[- ]thought|step[- ]by[- ]step|"
    r"^(input|given|context|task|prompt)\s*:|"
    # Truncated instruction echoes that pass the finish_reason==length
    # terminal-punctuation check (cycle 9.evening / 10.morning): e.g.
    # "Invent one brief personality trait for the newborn."
    r"^(invent|write|create|generate|output)\b|"
    r"personality trait for the newborn)",
    re.IGNORECASE,
)
_SCAFFOLD_LEADING_LIST_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def is_scaffold_text(text):
    """True if `text` looks like leaked chain-of-thought scaffold rather than
    a clean plain-text answer."""
    if not text:
        return False
    if _SCAFFOLD_MARKER_RE.search(text):
        return True
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) > 2:
        return True
    return any(_SCAFFOLD_LEADING_LIST_RE.match(ln) for ln in lines)


def extract_plain_answer(text):
    """Pull the real answer out of raw reasoning-model scaffold text: the
    answer follows the scaffold, so take the final non-empty line/segment and
    strip any leftover list markers or quoting."""
    if not text:
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    answer = _SCAFFOLD_LEADING_LIST_RE.sub("", lines[-1])
    answer = answer.strip(" \"'").strip()
    return answer or None


class MemoryStore:
    """Append-on-write / query-on-read memory with WM/STM/LTM tiers.

    Thread-safe (the Flask dev server handles think requests concurrently).
    Tier assignment is by salience + kind; the cleaner ages and prunes.
    """

    TIERS = ("working", "shortTerm", "longTerm")

    def __init__(self, path, mirror_path=None):
        self.path = path
        self.mirror_path = mirror_path
        self.entries = []
        self._next_id = 1
        self._since_persist = 0
        self._lock = threading.Lock()
        self._load_locked_startup()

    def _load_locked_startup(self):
        """Load persisted entries from `self.path` on construction so the
        store survives a server restart. Tolerates an absent file (fresh
        start) and a corrupt/unparseable file (start empty rather than
        crash the server) -- logged distinctly by the caller via the
        return value."""
        if not os.path.exists(self.path):
            self._load_status = ("absent", 0)
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            rows = data.get("entries") if isinstance(data, dict) else None
            self.import_entries(rows or [])
            self._load_status = ("loaded", len(self.entries))
        except (OSError, ValueError, TypeError, AttributeError):
            self.entries = []
            self._next_id = 1
            self._load_status = ("corrupt", 0)

    @staticmethod
    def _tier_for(salience, kind):
        if kind in ("summary", "autobiography"):
            return "longTerm"
        if salience >= 0.7:
            return "shortTerm"
        return "working"

    def store(self, agent, text, salience=0.5, kind="event", frame_tick=None,
              tier=None):
        text = (text or "").strip()
        if not text:
            return None
        try:
            salience = max(0.0, min(1.0, float(salience)))
        except (TypeError, ValueError):
            salience = 0.5
        entry = {
            "id": self._next_id,
            "agent": agent or "?",
            "text": text[:280],
            "vec": embed_text(text),
            "salience": salience,
            "kind": kind or "event",
            "tier": tier or self._tier_for(salience, kind),
            "frame_tick": frame_tick,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._next_id += 1
            self.entries.append(entry)
            self._trim_locked()
            self._since_persist += 1
            should_persist = self._since_persist >= MEMORY_PERSIST_EVERY
            if should_persist:
                self._since_persist = 0
        if should_persist:
            self._persist()
        return entry

    def query(self, agent=None, text="", top_k=5, tier=None, kinds=None):
        qv = embed_text(text)
        kinds = set(kinds) if kinds else None
        scored = []
        with self._lock:
            snapshot = list(self.entries)
        for e in snapshot:
            if agent and e["agent"] != agent:
                continue
            if tier and e["tier"] != tier:
                continue
            if kinds and e["kind"] not in kinds:
                continue
            # Cosine relevance plus a small salience/recency prior so important
            # and fresh memories surface even on a weak text match.
            score = _cosine(qv, e["vec"]) + 0.12 * e["salience"]
            scored.append((score, e["id"], e))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [e for _, _, e in scored[:max(1, int(top_k or 5))]]

    def recent(self, agent=None, limit=8, tier=None):
        with self._lock:
            snapshot = list(self.entries)
        if agent:
            snapshot = [e for e in snapshot if e["agent"] == agent]
        if tier:
            snapshot = [e for e in snapshot if e["tier"] == tier]
        return snapshot[-max(1, int(limit)):]

    def delete_where(self, *, agent=None, keyword=None, frame_from=None,
                     frame_to=None, kinds=None):
        """Delete entries matching structured filters. Thread-safe. Returns count
        deleted. At least one filter should be supplied by the caller."""
        kinds_set = set(kinds) if kinds else None
        kw_lower = keyword.lower() if isinstance(keyword, str) and keyword else None
        deleted = 0
        with self._lock:
            kept = []
            for e in self.entries:
                if agent and e.get("agent") != agent:
                    kept.append(e)
                    continue
                if kw_lower is not None and kw_lower not in (e.get("text") or "").lower():
                    kept.append(e)
                    continue
                ft = e.get("frame_tick")
                if frame_from is not None and (not isinstance(ft, int) or ft < frame_from):
                    kept.append(e)
                    continue
                if frame_to is not None and (not isinstance(ft, int) or ft > frame_to):
                    kept.append(e)
                    continue
                if kinds_set is not None and e.get("kind") not in kinds_set:
                    kept.append(e)
                    continue
                deleted += 1
            if deleted:
                self.entries = kept
                self._since_persist += 1
                should_persist = self._since_persist >= MEMORY_PERSIST_EVERY
                if should_persist:
                    self._since_persist = 0
            else:
                should_persist = False
        if should_persist:
            self._persist()
        return deleted

    def count_where(self, *, agent=None, keyword=None, frame_from=None,
                    frame_to=None, kinds=None):
        """Non-mutating count of entries that delete_where would remove."""
        kinds_set = set(kinds) if kinds else None
        kw_lower = keyword.lower() if isinstance(keyword, str) and keyword else None
        count = 0
        with self._lock:
            snapshot = list(self.entries)
        for e in snapshot:
            if agent and e.get("agent") != agent:
                continue
            if kw_lower is not None and kw_lower not in (e.get("text") or "").lower():
                continue
            ft = e.get("frame_tick")
            if frame_from is not None and (not isinstance(ft, int) or ft < frame_from):
                continue
            if frame_to is not None and (not isinstance(ft, int) or ft > frame_to):
                continue
            if kinds_set is not None and e.get("kind") not in kinds_set:
                continue
            count += 1
        return count

    def _trim_locked(self):
        """Drop the lowest-value entries once over the global cap."""
        if len(self.entries) <= MEMORY_MAX_ENTRIES:
            return
        # Keep summaries/autobiography and high-salience items; evict the rest
        # oldest-first until back under the cap.
        def value(e):
            keep = 1 if e["kind"] in ("summary", "autobiography") else 0
            return (keep, e["salience"], e["id"])
        self.entries.sort(key=value)
        overflow = len(self.entries) - MEMORY_MAX_ENTRIES
        self.entries = self.entries[overflow:]
        self.entries.sort(key=lambda e: e["id"])

    def clean(self):
        """Memory Cleaner: drop scaffold-poisoned entries (leaked
        chain-of-thought text from a reasoning model, see is_scaffold_text),
        then exact-duplicate texts per agent (keeping the most salient/newest
        copy), then re-trim to the cap. Deterministic and cheap so it can run
        often without burning LLM calls."""
        with self._lock:
            best = {}
            for e in self.entries:
                if is_scaffold_text(e["text"]):
                    continue
                key = (e["agent"], e["text"])
                prev = best.get(key)
                if prev is None or (e["salience"], e["id"]) > (prev["salience"], prev["id"]):
                    best[key] = e
            kept = sorted(best.values(), key=lambda e: e["id"])
            removed = len(self.entries) - len(kept)
            self.entries = kept
            self._trim_locked()
            self._since_persist = 0
        # Always flush on clean so memory.json reliably exists for inspection.
        self._persist()
        return removed

    def size(self):
        with self._lock:
            return len(self.entries)

    def export_entries(self):
        """Entries WITHOUT the recomputable `vec` field, for full-state
        persistence (Contract 3)."""
        with self._lock:
            return [{k: v for k, v in e.items() if k != "vec"} for e in self.entries]

    def import_entries(self, rows):
        """Rebuild the store from persisted rows, re-embedding each text.
        Replaces all current entries (used on resume from state.json)."""
        rebuilt = []
        max_id = 0
        for r in rows or []:
            try:
                text = (r.get("text") or "").strip()
                if not text:
                    continue
                eid = int(r.get("id") or 0)
                max_id = max(max_id, eid)
                sal = float(r.get("salience", 0.5))
                kind = r.get("kind") or "event"
                rebuilt.append({
                    "id": eid,
                    "agent": r.get("agent") or "?",
                    "text": text[:280],
                    "vec": embed_text(text),
                    "salience": max(0.0, min(1.0, sal)),
                    "kind": kind,
                    "tier": r.get("tier") or self._tier_for(sal, kind),
                    "frame_tick": r.get("frame_tick"),
                    "ts": r.get("ts") or datetime.now(timezone.utc).isoformat(),
                })
            except (TypeError, ValueError):
                continue
        with self._lock:
            self.entries = sorted(rebuilt, key=lambda e: e["id"])
            self._next_id = max_id + 1
            self._trim_locked()

    def clear(self):
        """Wipe all entries (used by engine.reset() so a reset starts the
        world with no carried-over agent memories)."""
        with self._lock:
            self.entries = []
            self._next_id = 1
            self._since_persist = 0
        self._persist()

    def tier_counts(self):
        counts = {t: 0 for t in self.TIERS}
        with self._lock:
            for e in self.entries:
                counts[e["tier"]] = counts.get(e["tier"], 0) + 1
        return counts

    def _persist(self):
        # self.path is the restart-stable store (simulation/memory_store.json)
        # -- this IS read back on the next construction (see
        # _load_locked_startup). self.mirror_path, if set, is a per-session
        # copy in the log dir kept purely for human inspection and is never
        # read back. Both omit the 128-float "vec" of each entry -- it's pure
        # bloat on disk and recomputable from the text.
        with self._lock:
            entries_copy = [
                {k: v for k, v in e.items() if k != "vec"}
                for e in self.entries
            ]
        payload = {
            "size": len(entries_copy),
            "entries": entries_copy,
        }
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            # Persistence must never break the simulation.
            pass
        if self.mirror_path:
            try:
                mirror_payload = dict(payload)
                mirror_payload["session_id"] = os.path.basename(
                    os.path.dirname(self.mirror_path))
                tmp = self.mirror_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(mirror_payload, fh, ensure_ascii=False)
                os.replace(tmp, self.mirror_path)
            except OSError:
                # The mirror is a debugging convenience only -- never let a
                # failure to write it affect the stable store.
                pass
