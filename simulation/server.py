# HOW TO RUN:
# 1. pip install flask flask-cors requests
# 2. Make sure Ollama is running at localhost:11434 with sim-smart and
#    sim-fast created/warm (uv run python scripts/ollama_setup.py --check to
#    verify; uv run python scripts/ollama_setup.py to (re)apply)
# 3. python server.py
# 4. Open http://127.0.0.1:5001 in Chrome or Firefox
#    (macOS AirPlay uses port 5000 and returns 403 — do not use 5000)

import atexit
import hashlib
import hmac
import json
import math
import os
import re
import shutil
import signal
import threading
import time
from collections import deque
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

# SYSTEM_PROMPT / SYSTEM_PROMPT_SLIM live in prompts.py (2026-07-24,
# docs/plan-ollama-migration.md Phase 6) -- the single source of truth both
# server.py and scripts/ollama_setup.py (--with-system) import from, so the
# rulebook text is never duplicated. See prompts.py's module docstring for
# why server.py itself can't be imported by the setup script directly
# (module-level side effects: SessionLogger() below opens a new session
# directory, and the live SimEngine is constructed further down).
import prompts as _prompts
SYSTEM_PROMPT = _prompts.SYSTEM_PROMPT
SYSTEM_PROMPT_SLIM = _prompts.SYSTEM_PROMPT_SLIM
COUNCIL_SYSTEM_PROMPT = _prompts.COUNCIL_SYSTEM_PROMPT

# to_ollama_body (the OpenAI-shaped-payload -> Ollama /api/chat wire-format
# conversion) lives in llm_wire.py (2026-07-24, docs/plan-ollama-migration.md
# Phase 4) so scripts/llm_replay_bench.py can import the exact conversion
# this module uses without importing server.py itself (see prompts.py's
# module docstring above for why -- same import-time side-effect reasoning
# applies here). Do not duplicate this mapping back into this module.
import llm_wire as _llm_wire
to_ollama_body = _llm_wire.to_ollama_body

app = Flask(__name__)
CORS(app)

# Ollama migration (2026-07-24, docs/plan-ollama-migration.md Phase 2):
# LM Studio is permanently unavailable. Native /api/chat is the only endpoint
# that actually honors think:false (Phase 0 finding #4 -- the OpenAI-compat
# /v1/chat/completions endpoint silently ignores it and would reintroduce the
# thinking-leak epidemic), so this repo targets it exclusively. See
# ollama_config.md for the full settings contract.
OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"

# Model routing (Phase 3 revision, 2026-07-24, docs/plan-ollama-migration.md):
# the two-model split is by WORKLOAD KIND, not by decision stakes. ALL
# decision turns (routine and high-stakes alike -- every call built by
# build_decision_payload / run_agent_decision) go to MODEL_SMART.
# MODEL_FAST serves ONLY background cognition: PIANO modules
# (run_piano_module), the memory summarizer/wiki merge, the meta system
# (autobiography/persona), and belief-pitch scoring -- i.e. every direct
# lm_complete() caller. is_high_stakes_turn/model_for_decision is UNCHANGED
# as a predicate: it still gates thinking (THINKING_SAMPLING vs
# NON_THINKING_SAMPLING), timeouts (THINKING_TIMEOUT_S vs DEFAULT_TIMEOUT_S),
# and max_tokens (HIGH_STAKES_MAX_TOKENS) -- only the MODEL_FAST branch of
# model_for_decision was removed so both branches resolve to MODEL_SMART for
# decisions specifically.
#
# Rationale (superseding the original "routine decisions on MODEL_FAST"
# design tried first): a live Phase 3 soak measured piano_module_drops
# climbing to ~25-38% (vs the ~9% pre-migration reference) and module
# latencies rising over the sample instead of falling, because routine
# decisions and PIANO modules were both queueing for sim-fast's
# OLLAMA_NUM_PARALLEL=3 slots -- decisions and background cognition were
# contending for the same small model's capacity. Keeping ALL decisions on
# sim-smart (which has its own, separate, uncontended slot pool) removes that
# contention; sim-fast now serves only the background-cognition workload it
# was sized for. Ids must be models created in Ollama (`ollama list` /
# GET /api/tags) -- scripts/ollama_setup.py is the canonical loader that
# creates/warms both. If a routed id isn't available, run_agent_decision
# treats that as a setup failure (see looks_like_model_not_found_error) and
# returns the offline fallback -- Ollama has no "local-model" alias to retry
# with like LM Studio did, so there's no silent single-model degrade path
# anymore.
#
# 2026-07-05 replay benchmark (100 logged prompts, docs/civilization-emergence-plan.md
# Part 6): qwen3.5-9b vs gemma-4-e4b — equal JSON/action validity (100%), but
# qwen halved move_to_district fixation (32% vs 65%), chose 9 distinct actions
# vs 7, and authored 20/20 valid blueprints vs 19/20, at ~3s/decision more.
# qwen emits via reasoning_content (empty content) — extract_decision_text
# already handles that path.
MODEL_SMART = "sim-smart"
MODEL_FAST = "sim-fast"

# Load-time rulebook (Phase 6, docs/plan-ollama-migration.md, shipped DARK --
# TASKS_PENDING item 2b revived). MODEL_SMART_SYS is a SEPARATE Ollama model,
# `sim-smart-sys`, generated by `scripts/ollama_setup.py --with-system` from a
# Modelfile that bakes prompts.py's SYSTEM_PROMPT text in as a Modelfile
# `SYSTEM` directive (never hand-copied -- prompts.py stays the one editable
# source). Verified live (ollama_config.md "Modelfile SYSTEM semantics"): a
# request WITHOUT a system message gets the baked SYSTEM applied; a request
# WITH an explicit system message overrides it entirely (replace, not
# concatenate). SYSTEM_PROMPT_AT_LOAD_TIME (below) is the flag that switches
# routine decision turns from "send MODEL_SMART + explicit SYSTEM_PROMPT" to
# "send MODEL_SMART_SYS + no system message" -- see build_decision_payload.
MODEL_SMART_SYS = "sim-smart-sys"

# DARK FLAG (2026-07-24, Phase 6): when True, routine decision turns (every
# build_decision_payload call except sprite/invention-only and the
# context-overflow slim retry) omit the system message and route to
# MODEL_SMART_SYS instead of MODEL_SMART, relying on that model's baked
# Modelfile SYSTEM directive for the rulebook. Slim-retry/invention/sprite
# turns are UNCHANGED: they always send their own explicit system prompt
# (SYSTEM_PROMPT_SLIM / INVENTION_SYSTEM_PROMPT / SPRITE_UPGRADE_SYSTEM_PROMPT
# respectively -- each differs from the baked SYSTEM_PROMPT, and an explicit
# system message overrides the baked one on either model) and stay on
# MODEL_SMART for cache-locality (no reason to pay for a model switch when
# the prompt is being sent explicitly anyway).
#
# Ships False. Do not flip without first: (1) running
# `uv run python scripts/ollama_setup.py --with-system` to (re)generate and
# `ollama create sim-smart-sys`, (2) an A/B soak comparing fallback rate
# (bad_response + role_fallback) and action distribution against a
# flag-off session of similar length -- the "model forgets a distant system
# prompt" risk is the tripwire (see specs/03-cognition.md and
# ollama_config.md "Load-time rulebook (dark)").
SYSTEM_PROMPT_AT_LOAD_TIME = False

# Startup assert (Phase 3, "permanent two-model MUST" per the migration
# plan): implemented as a loud warning rather than a hard crash -- a config
# regression here should degrade decision/module quality, not take the 24/7
# sim server offline. If this ever prints, MODEL_FAST silently collapsed back
# onto MODEL_SMART somewhere upstream of this module (env override, hotfix,
# etc.) and the two-model split is no longer in effect.
if MODEL_FAST == MODEL_SMART:
    print("[server] WARNING: MODEL_FAST == MODEL_SMART -- two-model design "
          "violated (see docs/plan-ollama-migration.md Phase 3). Continuing "
          "to start rather than crashing the 24/7 server, but PIANO modules "
          "and routine decisions are now sharing the smart model's queue.")

# Thinking control (2026-07-11): routine villager turns run with reasoning
# DISABLED -- the old '"thinking": {"type": "disabled"}' payload key was
# Anthropic-API format that LM Studio ignores (every routine decision was
# emitted through reasoning_content). Probed live against this LM Studio
# build: top-level '"reasoning_effort": "none"' is the knob it honors;
# chat_template_kwargs={"enable_thinking": false} and Qwen's /no_think soft
# switch are both ignored (known bug, lmstudio-bug-tracker #1990). High-stakes
# turns (elder / invention / sprite / invention-REQUIRED -- the MODEL_SMART
# set in model_for_decision) keep thinking ON, which makes the smart/fast
# routing meaningful even while both tiers point at one model.
# Replay bench (scripts/llm_replay_bench.py, 40 calls): thinking-leak
# 100% -> 0%, JSON validity 100% -> 100%, action diversity unchanged.
#
# Ollama migration (2026-07-24): the LM Studio-specific "reasoning_effort"
# knob above is history -- Ollama's native /api/chat has its own top-level
# "think" boolean, verified live (Phase 0 finding #4) to actually suppress
# reasoning output entirely (no separate `thinking` field, no <think> tags in
# content). DISABLE_THINKING_ROUTINE now means "send think:false on routine
# turns" (see to_ollama_body / build_decision_payload); the historical intent
# (routine turns never spend budget on reasoning) is unchanged.
DISABLE_THINKING_ROUTINE = True

# Thinking on high-stakes turns is DISABLED (reverted 2026-07-14, Phase 3 --
# see .claude/plans/only-create-the-plan-linear-iverson.md Phase 2/3). Phase 1
# history: a full session (6,320 calls) measured 57% of high-stakes/thinking
# turns -- 65% of the elder's -- returning bad_response (finish_reason
# "length", empty content), then falling back to a canned action. Cause: with
# thinking ON the model spends its whole max_tokens budget (512-1024) on
# reasoning_content before emitting the decision JSON. Phase 1 fixed the
# epidemic by disabling thinking on high-stakes turns entirely. Phase 2 tried
# fixing the root cause instead: scripts/lms_load.py dropped to parallel 2
# (10,000 tokens/slot, same total VRAM) and HIGH_STAKES_MAX_TOKENS=1600 gave
# the completion room to finish, so thinking was re-enabled and measured
# against live traffic. Phase 3 verdict (2026-07-14): a live analysis of 48
# diverse high-stakes samples (assign_task, propose_blueprint,
# sage_review_blueprint, approve_blueprint, upgrade_structure,
# contribute_resources, collect_resource, move_to_district) found ZERO
# measurable reasoning benefit -- with thinking on, the model emits the same
# direct JSON answer, just routed through reasoning_content instead of
# content (THINKING_SAMPLING doesn't set reasoning_effort, so nothing bounds
# or shapes the "reasoning"). The only sample showing genuine descriptive
# text was submit_structure_sprite, an unrelated creative-task pattern
# (always high-stakes regardless of this flag). Since thinking has no
# measured benefit but costs 33% concurrency (parallel 3->2), reverted to
# THINKING_ENABLED_HIGH_STAKES=False and parallel=3.
THINKING_ENABLED_HIGH_STAKES = False

# Qwen-recommended sampling pins (model card). Only temperature was sent
# before; top_p/top_k/min_p silently followed whatever LM Studio preset was
# active, which drifts across app updates and reloads. Temperatures stay the
# behavior-tuned values below. Under Ollama these route into the request's
# "options" object (see to_ollama_body) rather than top-level payload keys;
# the dict shape here is unchanged since it's just merged into the internal
# payload before conversion.
NON_THINKING_SAMPLING = {"top_p": 0.8, "top_k": 20, "min_p": 0}
THINKING_SAMPLING = {"top_p": 0.95, "top_k": 20}

# Experiment lever (off by default): a small presence penalty on routine
# turns may further cut move_to_district fixation (32% share in the
# 2026-07-05 replay benchmark above). Flip to e.g. 0.5 and compare with
# scripts/llm_replay_bench.py before adopting.
ROUTINE_PRESENCE_PENALTY = 0.0

# Phase D model-experiment hook (plan Part 6 / copilot-audit C4): invention-only
# calls override the decision defaults (temperature 0.4 / max_tokens 512).
# The 2026-07-09 council investigation found 32/171 invention completions
# hitting the 512-token ceiling (finish_reason "length") mid-JSON -- the
# dominant cause of "blueprint must be an object" and missing-function
# rejections, since the model runs out of budget before closing the object.
# 1024 gives room for needs + function + an optional sprite without changing
# routine (non-invention) turns. Temperature 0.6 (up from the routine 0.4)
# gives the council fan-out proposal diversity instead of 3 members
# converging on the same idea.
INVENTION_TEMPERATURE = 0.6
INVENTION_MAX_TOKENS = 1024

# Phase 2 (2026-07-14, see .claude/plans/only-create-the-plan-linear-iverson.md):
# max_tokens for high-stakes turns with thinking re-enabled. A live probe
# showed a thinking turn needs ~950-1,300 completion tokens to finish
# reasoning_content and still emit the decision JSON; 1600 leaves headroom.
# 6,163 tokens (worst-case measured prompt) + 1600 = 7,763 < 10,000 (the new
# per-slot budget at parallel 2), so it fits without truncation.
# Phase 3 (2026-07-14): THINKING_ENABLED_HIGH_STAKES reverted to False, so the
# override below is currently dead code (only applies when thinking_active is
# true). Left in place in case thinking on high-stakes turns is revisited.
HIGH_STAKES_MAX_TOKENS = 1600

# Request timeout (seconds). Routine decisions measured median ~18s / p90 ~22s
# in the 2026-07-07 session, well under the old flat 30s -- but invention-only
# turns (bigger prompt: function-block schema, tier rules, sprite instructions
# + few-shot example) measured median ~32s / max ~33.6s, so ~71% of them were
# timing out, logged as "llm offline", and silently falling back to a
# non-propose action -- the actual reason invention councils kept dissolving
# with zero proposals (12 dissolutions, only 2 successful proposals logged).
# Invention turns are rare (a few per hour) so a generous timeout costs
# nothing; DEFAULT_TIMEOUT_S stays tight so routine throughput is unaffected.
#
# THINKING_TIMEOUT_S covers ALL high-stakes turns (see is_high_stakes_turn),
# not just invention/sprite ones -- elder-role and invention-status-REQUIRED
# turns also keep THINKING_SAMPLING on and route to MODEL_SMART, so they're
# just as slow. Measured ~12-20s median under 3-way concurrency; 75s covers
# p99 plus queueing behind other in-flight thinking turns.
DEFAULT_TIMEOUT_S = 30
THINKING_TIMEOUT_S = 75

COUNCIL_LLM_ACTIONS = frozenset({
    "propose_blueprint", "approve_blueprint", "reject_blueprint", "sage_review_blueprint",
})


# A3: high_stakes_reason values (shadow-logged by sim_engine._build_think_payload
# since A1) that are ALSO allowed to trigger thinking/MODEL_SMART/THINKING_TIMEOUT_S,
# on top of the original unbudgeted four (sprite/invention/elder/REQUIRED).
# Enabled from A1 shadow observations: rare, high-value events worth the extra
# latency. Deliberately excluded:
#   - "elder_blueprint_review": redundant -- elder turns are already high-stakes
#     via the role check below, so this reason never adds a NEW thinking turn.
#   - "repeated_rejections": too frequent/noisy to spend the thinking budget on;
#     it fires often enough that it would dominate EXTRA_THINKING_PER_WINDOW.
HIGH_STAKES_ENABLED_REASONS = frozenset({"emergency", "election", "treaty_vote"})

# Rolling-window budget for the EXTRA thinking turns unlocked by
# HIGH_STAKES_ENABLED_REASONS only -- the original four is_high_stakes_turn
# conditions (sprite/invention/elder/REQUIRED) stay unbudgeted. Bounds how much
# extra MODEL_SMART/THINKING_TIMEOUT_S load the new (rare-but-not-zero) reasons
# can add per unit time, since run_agent_decision runs on worker threads (up to
# MAX_CONCURRENT_LLM=2 in flight).
EXTRA_THINKING_PER_WINDOW = 4
EXTRA_THINKING_WINDOW_S = 60
_extra_thinking_lock = threading.Lock()
_extra_thinking_timestamps = deque()


def _consume_extra_thinking_budget():
    """Thread-safe rolling-window limiter. Returns True (and reserves a slot)
    if under EXTRA_THINKING_PER_WINDOW turns within the last
    EXTRA_THINKING_WINDOW_S seconds, else False."""
    now = time.monotonic()
    with _extra_thinking_lock:
        while _extra_thinking_timestamps and now - _extra_thinking_timestamps[0] > EXTRA_THINKING_WINDOW_S:
            _extra_thinking_timestamps.popleft()
        if len(_extra_thinking_timestamps) < EXTRA_THINKING_PER_WINDOW:
            _extra_thinking_timestamps.append(now)
            return True
        return False


def _base_high_stakes(data):
    """The original unbudgeted MODEL_SMART set: turns that keep thinking
    enabled and route to the smart tier regardless of budget."""
    if data.get("sprite_design_only"):
        return True
    if data.get("invention_only"):
        return True
    if data.get("council_turn") and (data.get("daily_council") or {}).get("phase") == "verdict" \
            and (data.get("role") or "").lower() == "elder":
        return True
    if (data.get("role") or "").lower() == "elder":
        return True
    if str(data.get("invention_status") or "").startswith("REQUIRED"):
        return True
    return False


def is_high_stakes_turn(data):
    """The MODEL_SMART set: turns that keep thinking enabled and route to the
    smart tier. Kept as a predicate (not a MODEL_SMART == MODEL_FAST string
    compare) because both tiers currently resolve to the same model id.

    is_high_stakes_turn is called from multiple places per request
    (model_for_decision via build_decision_payload, the timeout choice in
    run_agent_decision, and again on the context-overflow slim retry). The
    HIGH_STAKES_ENABLED_REASONS path consumes a stateful budget, so it must be
    resolved exactly ONCE per request -- see resolve_high_stakes(), which
    stamps the outcome into data["_high_stakes_resolved"]. When that stamp is
    present, every call here (including this one) just echoes it so all call
    sites agree; only an unstamped `data` (e.g. ad-hoc/test calls) falls back
    to the unbudgeted base predicate."""
    if "_high_stakes_resolved" in data:
        return data["_high_stakes_resolved"]
    return _base_high_stakes(data)


def resolve_high_stakes(data):
    """Resolve is_high_stakes_turn ONCE per request and stamp the result into
    `data` so downstream is_high_stakes_turn() calls agree without
    re-consuming the extra-thinking budget. Call this first thing in
    run_agent_decision, before build_decision_payload/model_for_decision run.

    Returns (resolved: bool, capped: bool) where `capped` is True only when a
    HIGH_STAKES_ENABLED_REASONS turn qualified but was denied by the budget
    (for log_lm's "high_stakes_capped" field)."""
    if _base_high_stakes(data):
        data["_high_stakes_resolved"] = True
        return True, False
    capped = False
    resolved = False
    if data.get("high_stakes_reason") in HIGH_STAKES_ENABLED_REASONS:
        if _consume_extra_thinking_budget():
            resolved = True
        else:
            capped = True
    data["_high_stakes_resolved"] = resolved
    return resolved, capped


def model_for_decision(data):
    """Phase 3 revision: every decision turn -- routine or high-stakes --
    routes to MODEL_SMART unless a Divine Matrix agent_sampling override is
    active (sim-fast forces MODEL_FAST; sim-smart stays on the smart tier).
    is_high_stakes_turn(data) is still computed by callers
    (build_decision_payload, run_agent_decision's timeout choice, the slim
    retry) to select thinking/timeout/max_tokens, but no longer selects the
    model id here except via divine_sampling. MODEL_FAST is otherwise
    reserved for background cognition (PIANO modules, memory summarizer/wiki
    merge, meta system, belief pitch -- every direct lm_complete() caller),
    never for decisions. See the MODEL_SMART/MODEL_FAST comment block above."""
    sampling = data.get("divine_sampling")
    if isinstance(sampling, dict):
        model_key = sampling.get("model")
        if model_key == "sim-fast":
            return MODEL_FAST
        if model_key == "sim-smart":
            return MODEL_SMART
    return MODEL_SMART


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


session_logger = SessionLogger(os.path.dirname(os.path.abspath(__file__)))
atexit.register(session_logger.flush_benchmarks)
print(f"[server] Logging session to: {session_logger.dir}")


# --- Phase 1: in-process vector memory store (replaces ChromaDB/Docker) ---
# CMA's shared vector store + Sid's WM/STM/LTM tiers, kept in-process to honor
# the no-external-service ethos. Embedding is a deterministic hashing trick
# (bag-of-tokens hashed into a fixed dimension, L2-normalized) so cosine
# similarity == dot product. Swappable for a real embedding model / Chroma
# later behind the identical /memory/* endpoints.
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
# memory and re-enters every future prompt via compose_memory(). These two
# helpers extract the real answer and reject anything that still looks like
# leaked scaffolding, for both the plain-text LLM path (lm_complete) and the
# memory stores that may already hold poisoned entries (MemoryStore.clean,
# and the engine's longTerm lists -- see _ENGINE_DEPS below).
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
_SCAFFOLD_LEADING_LIST_RE = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s+")


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


# MemoryStore is constructed against a restart-stable path (simulation/
# memory_store.json, next to state.db) so semantic recall survives a server
# restart instead of silently starting empty every time; the per-session
# memory.json in the log dir is kept as a mirror for human inspection only
# (see MemoryStore._persist).
MEMORY_STORE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "memory_store.json")
memory_store = MemoryStore(
    MEMORY_STORE_PATH, mirror_path=os.path.join(session_logger.dir, "memory.json"))
_load_status, _load_count = getattr(memory_store, "_load_status", ("absent", 0))
print(f"[server] MemoryStore {_load_status} ({_load_count} entries) from {MEMORY_STORE_PATH}")
session_logger.log_benchmark("memory_store_loaded", _load_count)

# --- Blueprint validation constants ---
GATHER_ZONES = {"farm", "forest", "village", "market", "beach", "cave", "ocean"}
BASE_RESOURCE_IDS = {"food", "wood", "gold"}
SEED_PROJECT_IDS = {"house", "farm_plot", "workshop", "wall"}
TERRAFORM_PROJECT_IDS = frozenset({"plant_grove", "clear_field", "extend_beach"})
KIND_TO_TERRAFORM = {"farm": "clear_field", "forest": "plant_grove", "beach": "extend_beach"}
TERRAFORM_KIND = {v: k for k, v in KIND_TO_TERRAFORM.items()}
RESOURCE_TO_TERRAFORM = {
    "wood": "plant_grove", "herbs": "plant_grove",
    "food": "clear_field", "fish": "extend_beach",
}


def _district_kind_map(agent_data):
    out = {}
    for d in agent_data.get("known_districts") or []:
        if isinstance(d, dict) and d.get("id"):
            out[d["id"]] = d.get("kind")
    return out


def _fuzzy_terraform_id(raw):
    """Map display names and slugs to canonical terraform template ids."""
    if not raw:
        return None
    s = str(raw).strip().lower()
    if s in TERRAFORM_PROJECT_IDS:
        return s
    slug = s.replace(" ", "_").replace("-", "_")
    if slug in TERRAFORM_PROJECT_IDS:
        return slug
    compact = s.replace(" ", "").replace("_", "").replace("-", "")
    for tid in TERRAFORM_PROJECT_IDS:
        if compact == tid.replace("_", ""):
            return tid
    aliases = {
        "plant grove": "plant_grove", "plantgrove": "plant_grove",
        "clear field": "clear_field", "clearfield": "clear_field",
        "extend beach": "extend_beach", "extendbeach": "extend_beach",
    }
    return aliases.get(s) or aliases.get(compact)


def _infer_terraform_decision(decision, agent_data):
    """Promote district/resource targets to template ids (models name places)."""
    district_map = _district_kind_map(agent_data)
    target = decision.get("target")
    target_district = decision.get("target_district")

    if target in TERRAFORM_PROJECT_IDS:
        if target_district and target_district not in district_map:
            decision["target_district"] = None
        return decision, None

    if target and target in district_map:
        tmpl = KIND_TO_TERRAFORM.get(district_map[target])
        if tmpl:
            decision["target"] = tmpl
            decision["target_district"] = target
            return decision, None

    if target_district and target_district in district_map:
        tmpl = KIND_TO_TERRAFORM.get(district_map[target_district])
        if tmpl:
            decision["target"] = tmpl
            return decision, None

    fuzzy = _fuzzy_terraform_id(target)
    if fuzzy:
        decision["target"] = fuzzy
        return decision, None

    known_resources = agent_data.get("known_resource_ids") or []
    if target and target in known_resources:
        tmpl = RESOURCE_TO_TERRAFORM.get(target)
        if tmpl:
            decision["target"] = tmpl
            want_kind = TERRAFORM_KIND[tmpl]
            if target_district and district_map.get(target_district) == want_kind:
                decision["target_district"] = target_district
            else:
                current = agent_data.get("current_district")
                if current and district_map.get(current) == want_kind:
                    decision["target_district"] = current
                else:
                    match = next((did for did, k in district_map.items() if k == want_kind), None)
                    if match:
                        decision["target_district"] = match
            return decision, None

    return None, f"could not infer terraform template from target {target!r}"

VISUAL_STYLES = {"house", "farm_plot", "workshop", "wall", "generic"}
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,24}$")
FUNCTION_EFFECT_KEYS = ("produces", "boosts", "unlocks", "stores", "houses",
                        "shelter", "light", "upkeep")
VALID_PRODUCE_SCOPES = {"village", "district"}
VALID_BOOST_KINDS = {"gather", "craft"}
VALID_BOOST_SCOPES = {"village", "district"}
VALID_UNLOCK_KINDS = {"craft", "transit"}
MAX_PENDING_BLUEPRINTS = 5
MAX_PENDING_ROLES = 5
MAX_EMERGENT_ROLES = 8
MAX_APPROVED_CUSTOM = 15
MAX_CUSTOM_RESOURCES = 10
# Phase D (TECH_TREE_ENABLED): blueprint tech-tier bounds. Tier gating only
# runs when the caller passes a village_tier (the engine passes None with the
# flag off, so flag-off validation is unchanged).
MAX_TECH_TIER = 3
# Role definitions are the single source of truth in roles.json (also served to
# the browser as /roles.js). The server derives its role maps from it so the
# client and server can never drift.
_ROLES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "roles.json")
with open(_ROLES_PATH, encoding="utf-8") as _f:
    ROLES = json.load(_f)

# role -> preferred project (string or list, mirroring the client).
ROLE_PROJECT = {role: d["preferredProject"] for role, d in ROLES.items()}

# --- Structured output (Ollama `format`, via the internal response_format shape) ---
# Constrain the model to emit a conforming JSON decision at decode time, which
# largely eliminates the malformed-JSON fallback path. "json_schema" shapes every
# field; "json_object" only guarantees syntactic validity; "off" disables it.
# extract_json_decision/normalize_decision remain as defense in depth regardless.
STRUCTURED_OUTPUT_MODE = "json_schema"

# Full action superset (mirrors AVAILABLE_ACTIONS in index.html). Per-agent
# availability is still enforced by normalize_decision/role_fallback_action.
# World-expansion plan: the fixed move_to_farm/move_to_market/etc. members were
# replaced by a single generic move_to_district (target names a district id,
# or a legacy kind name -- sim_engine.py's _resolve_target_district resolves
# either). Hardcoding a move_to_X per district doesn't scale once districts
# can be founded at runtime rather than fixed at code-authoring time.
DECISION_ACTIONS = [
    "move_to_district", "move_to_agent",
    "collect_resource", "talk_to_nearby", "found_belief", "trade_resource",
    "start_project", "contribute_resources", "build_structure",
    "start_terraform",
    # Phase C (GOODS_ENABLED): structure upkeep. The engine filters it from
    # available_actions when the flag is off; normalize passes it through and
    # the engine surfaces reasons (lastRepairRejection).
    "repair_structure",
    "upgrade_structure",
    "submit_structure_sprite",
    "propose_blueprint", "approve_blueprint", "reject_blueprint", "sage_review_blueprint",
    "assign_task", "change_role", "rest",
    # Survival (#2) and crafting (#4) actions. The client gates these by flag,
    # but the schema enum is a fixed superset (normalize_decision filters).
    "heal_agent",
    "craft_item", "propose_recipe", "approve_recipe", "reject_recipe",
    # CMA + Sid enhancement actions (emergent roles + collective rules/voting).
    "switch_role", "propose_role", "approve_role", "reject_role",
    "propose_rule", "vote_rule", "repeal_rule",
    # Cemetery/burial (permanent-death handling): the engine filters it from
    # available_actions when CEMETERY_ENABLED is off, same pattern as repair_structure.
    "bury_agent",
    # Path 1: composable tiles, terrain mutation, diplomacy treaties.
    "place_block", "remove_block", "dig_terrain", "plant_terrain",
    "propose_treaty", "vote_treaty",
    "deliver_caravan",
    # Huntable wildlife (WILDLIFE_ENABLED): engine offers only when prey is in range.
    "hunt_wildlife",
    "confront_agent",
    # Daily Council Assembly. Offered only to a seated attendee in-session.
    "council_speak", "council_propose", "council_vote",
]

# Sprite grid bounds (also enforced post-hoc by validate_sprite_block()); defined
# here, ahead of DECISION_SCHEMA, so the schema below can reference them directly.
SPRITE_GRID_MIN = 4
SPRITE_GRID_MAX = 14

# Loose shape only; validate_blueprint() stays the authority on blueprint detail.
DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    # normalize_decision tolerates their absence (action defaults to "rest"),
    # but requiring them at decode time means the grammar itself guarantees
    # the two fields every log/consumer relies on.
    "required": ["action", "reasoning"],
    "properties": {
        "action": {"type": "string", "enum": DECISION_ACTIONS},
        "target": {"type": ["string", "null"]},
        "target_district": {"type": ["string", "null"]},
        "message": {"type": ["string", "null"]},
        "feeling": {"type": ["string", "null"]},
        "topic": {"type": ["string", "null"]},
        "kind": {"type": ["string", "null"], "enum": ["rule", "blueprint", "idea", None]},
        "title": {"type": ["string", "null"]},
        "detail": {"type": ["string", "null"]},
        "new_role": {"type": ["string", "null"]},
        "relationship_update": {
            "type": ["object", "null"],
            "additionalProperties": {"enum": ["ally", "neutral", "rival"]},
        },
        "reasoning": {"type": "string"},
        "blueprint": {
            "type": ["object", "null"],
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "needs": {"type": "object"},
                "new_resources": {"type": "array"},
                "visual_style": {"type": "string"},
                "sprite": {"type": ["object", "null"]},
                "function": {"type": "object"},
            },
        },
        "recipe": {
            "type": ["object", "null"],
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "inputs": {"type": "object"},
                "station": {"type": ["string", "null"]},
            },
        },
        "role": {
            "type": ["object", "null"],
            "properties": {
                "slug": {"type": "string"},
                "name": {"type": "string"},
                "specialty": {"type": "array", "items": {"type": "string"}},
                "preferredProject": {"type": ["string", "array", "null"]},
                "skill": {"type": "string"},
            },
        },
        "rule": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "kind": {"type": "string"},
                "value": {"type": ["number", "string", "null"]},
                "description": {"type": ["string", "null"]},
                "tariff": {"type": ["number", "null"], "minimum": 0, "maximum": 0.25},
                "supersedes": {"type": ["string", "null"]},
                "effect": {
                    "type": ["object", "null"],
                    "additionalProperties": False,
                    "required": ["subject", "condition", "modifier"],
                    "properties": {
                        "subject": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "resource": {"type": "string"},
                                "role": {"type": "string"},
                                "district": {"type": "string"},
                                "action": {"type": "string", "enum": [
                                    "collect_resource", "contribute_resources", "craft_item"]},
                            },
                            "oneOf": [
                                {"required": ["resource"], "properties": {"resource": {"type": "string"}}},
                                {"required": ["role"], "properties": {"role": {"type": "string"}}},
                                {"required": ["district"], "properties": {"district": {"type": "string"}}},
                                {"required": ["action"], "properties": {
                                    "action": {"type": "string", "enum": [
                                        "collect_resource", "contribute_resources", "craft_item"]}}},
                            ],
                        },
                        "condition": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "action": {"type": "string", "enum": [
                                    "collect_resource", "contribute_resources", "craft_item"]},
                                "resource": {"type": "string"},
                                "role": {"type": "string"},
                                "district": {"type": "string"},
                            },
                        },
                        "modifier": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["kind", "value"],
                            "properties": {
                                "kind": {"type": "string", "enum": ["add"]},
                                "value": {"type": "integer", "minimum": 1, "maximum": 3},
                            },
                        },
                    },
                },
            },
        },
        "belief": {
            "type": ["object", "null"],
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "tenet": {"type": "string"},
                "affinity": {"type": "array", "items": {"type": "string"}},
            },
        },
        "belief_pitch": {
            "type": ["object", "null"],
            "properties": {
                "belief_id": {"type": "string"},
                "pitch": {"type": "string"},
            },
        },
        "vote": {"type": ["string", "null"]},
        "candidate": {"type": ["string", "null"]},
        "sage_decision": {"type": ["string", "null"], "enum": ["approve", "deny", None]},
        # Bounded at the grammar level (not just post-hoc validation) because an
        # unbounded schema lets generation run away to 30-100+ row grids and get
        # cut off by max_tokens mid-JSON (observed: 5/5 length-truncation failures
        # in a Phase 0 probe). Mirrors the limits validate_sprite_block() enforces.
        "sprite": {
            "type": ["object", "null"],
            "properties": {
                "palette": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 5,
                    "items": {"type": "string"},
                },
                "grid": {
                    "type": "array",
                    "minItems": SPRITE_GRID_MIN,
                    "maxItems": SPRITE_GRID_MAX,
                    "items": {
                        "type": "string",
                        "minLength": SPRITE_GRID_MIN,
                        "maxLength": SPRITE_GRID_MAX,
                    },
                },
            },
        },
        # Required on every decision turn while voice_guidance_active is true
        # (binding Voice guidance). Missing/invalid values are synthesized in
        # synthesize_divine_response() — not rejected.
        "divine_response": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "stance": {"type": "string", "enum": ["follow", "continue"]},
                "reason": {"type": "string"},
            },
            "required": ["stance", "reason"],
        },
    },
}

# Flipped off for the rest of the session if Ollama rejects the format/
# json-schema field. Ollama's `format` param is stable (Phase 0 finding #2 --
# a full JSON schema was honored in every trial), so this auto-degrade is a
# safety net now rather than an expected path, but it stays in place.
_structured_output_enabled = STRUCTURED_OUTPUT_MODE != "off"

# Flipped off (and the setup-failure warning logged once) if Ollama doesn't
# have a routed model id (MODEL_SMART/MODEL_FAST) created. Unlike LM Studio,
# Ollama has no generic "local-model" alias to retry with -- a missing model
# is a setup failure (see looks_like_model_not_found_error and its call site
# in run_agent_decision), so this flag no longer gates a retry; it only
# avoids repeat-logging the same warning every subsequent call.
_model_routing_enabled = True

# Bounds the WORST CASE across every requests.post(OLLAMA_CHAT_URL, ...) call
# site inside a single run_agent_decision() invocation (currently 3: the
# initial call, the format-degrade retry, and the context-overflow slim
# retry -- a later phase adds a 4th). MAX_CONCURRENT_LLM = 3 (sim_engine.py)
# means one agent stuck making sequential calls can otherwise hold a scarce
# worker slot for far longer than one call's timeout; this constant is the
# hard ceiling _post_ollama() (see run_agent_decision) enforces per turn.
LLM_CALLS_PER_TURN_MAX = 4

# Phase 5: retry once (budget permitting) on an answer-quality failure --
# unparseable JSON or normalize_decision stamping _fallback -- with a
# concrete-reason feedback line appended to the retry's user prompt. Default
# on. With this flag off, run_agent_decision behaves exactly as it did before
# this phase (no retry, decision_retries always 0 in llm.jsonl). Deliberately
# does NOT cover network-level failures (llm offline/timeout, compute_error,
# server_error, model_not_found, llm budget exhausted) -- see
# run_agent_decision's _request_error_tag docstring on why Ollama-side
# orphaned generations make a network retry counterproductive there.
DECISION_RETRY_ENABLED = True

# Phase 6 (Fix 5): when DECISION_RETRY_ENABLED's retry is exhausted with no
# usable decision AND the failure was answer-quality (not network -- see
# _request_error_tag's docstring; llm offline/timeout, compute_error,
# server_error, model_not_found, and llm budget exhausted all return before
# ever reaching a fallback call site, so they can never trigger this), offer
# the model a tiny "pick one of these safe options" choice among the role
# ladder's candidates (role_fallback_candidates) instead of always taking the
# highest-priority one silently. Default on. With this flag off,
# run_agent_decision behaves exactly as it did before this phase, including
# no fallback_* fields in llm.jsonl.
FALLBACK_AI_CHOICE_ENABLED = True

# Small, fixed timeout for the Fix 5 choice call -- it is a single-letter
# tiebreak prompt (see _fallback_ai_choice), never the full decision schema,
# so it does not need DEFAULT_TIMEOUT_S/THINKING_TIMEOUT_S headroom. Never
# retried on timeout; the first (highest-priority) candidate is used instead.
FALLBACK_AI_CHOICE_TIMEOUT_S = 10


class LLMBudgetExhausted(Exception):
    """Raised by _post_ollama (inside run_agent_decision) when a single turn
    has already spent its LLM_CALLS_PER_TURN_MAX budget. Deliberately NOT a
    requests.exceptions.RequestException subclass so it can never be mistaken
    for a network failure (llm offline/llm timeout) at any call site."""


def _ollama_error_parts(lm_body):
    """Extract (message, type) from an Ollama /api/chat error body. Modern
    Ollama (0.32.3, per Phase 0 finding #5) returns a structured
    {"error": {"code":, "message":, "type":, ...}} object on HTTP 400s (e.g.
    type "exceed_context_size_error"); tolerate a bare string too in case a
    future/older build differs."""
    err = lm_body.get("error") if isinstance(lm_body, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err), err.get("type")
    return str(err or ""), None


def looks_like_model_not_found_error(http_status, lm_body):
    """True when Ollama rejected the request because the requested model id
    isn't created/pulled (as opposed to any other error) -- a setup failure,
    not a transient condition (see run_agent_decision's handling)."""
    text, _ = _ollama_error_parts(lm_body) if isinstance(lm_body, dict) else ("", None)
    low = text.lower()
    return bool(low) and "model" in low and any(
        k in low for k in ("not found", "no model", "failed to load", "unknown model"))


def build_response_format():
    """The internal response_format payload field for the current mode, or
    None. Kept in this OpenAI-style shape (rather than Ollama's flatter
    `format` field) so the rest of this module's payload-building code is
    unchanged; to_ollama_body() extracts the actual JSON schema object out of
    the "json_schema" nesting when converting to the wire request."""
    if not _structured_output_enabled:
        return None
    if STRUCTURED_OUTPUT_MODE == "json_schema":
        return {
            "type": "json_schema",
            "json_schema": {"name": "agent_decision", "schema": DECISION_SCHEMA},
        }
    if STRUCTURED_OUTPUT_MODE == "json_object":
        return {"type": "json_object"}
    return None


def looks_like_response_format_error(http_status, lm_body):
    """True when Ollama rejected the request specifically over the `format`
    (JSON-schema) field. Rare in practice (see _structured_output_enabled's
    comment) but the auto-degrade safety net stays in place regardless."""
    text, _ = _ollama_error_parts(lm_body) if isinstance(lm_body, dict) else ("", None)
    if http_status == 400 or text:
        low = text.lower()
        return any(k in low for k in ("response_format", "format", "json_schema", "grammar", "schema"))
    return False


# to_ollama_body is now imported from llm_wire.py -- see the import near the
# top of this module (just below the SYSTEM_PROMPT/SYSTEM_PROMPT_SLIM import).

# SYSTEM_PROMPT / SYSTEM_PROMPT_SLIM moved to prompts.py (2026-07-24,
# docs/plan-ollama-migration.md Phase 6) so scripts/ollama_setup.py can
# import the exact rulebook text without importing server.py itself (see
# prompts.py's module docstring for why). Imported near the top of this
# module -- see the `import prompts as _prompts` block above.

# Dedicated, minimal system prompt for invention-only turns (see
# build_invention_prompt / _maybe_invention_backstop). SYSTEM_PROMPT/SLIM carry
# ~20 rules (talk, ecology, survival, crafting, sage priority, roles, voting,
# market, population, knowledge) that are irrelevant to authoring a blueprint
# and cost ~3k prompt tokens on every council member's turn for nothing --
# the 2026-07-09 investigation found invention turns still shipped the full
# rulebook while only ever emitting propose_blueprint. This keeps just the
# output-format contract and the blueprint schema/example, cutting prompt
# size by roughly 85% so the context-overflow retry actually has headroom to
# matter and so INVENTION_MAX_TOKENS goes toward the blueprint, not rules the
# model never needed to see.
INVENTION_SYSTEM_PROMPT = """You are an autonomous agent in a pixel-art village simulation.
This turn your ONLY job is to invent a new structure by responding with propose_blueprint. Do not use any other action.

Respond with ONLY valid JSON. No markdown, no explanation, no extra text.
Do not use chain-of-thought or reasoning — output the JSON object immediately.
The JSON must match this structure exactly:
{
  "action": "propose_blueprint",
  "target": null,
  "target_district": null,
  "message": null,
  "new_role": null,
  "relationship_update": null,
  "reasoning": "<one short sentence>",
  "blueprint": <blueprint object, see schema below>
}

BLUEPRINT object schema:
{
  "id": "library",                       // ^[a-z][a-z0-9_]{1,24}$ -- must NOT match any id already taken (see below)
  "name": "Library",                     // 1-32 chars
  "needs": {"wood": 4, "paper": 2},      // 1-8 entries, each amount 1-5
  "new_resources": [                      // 0-3 items; omit entirely if you aren't adding a resource
    {"id": "paper", "name": "Paper", "gather_zone": "forest", "color": "#E8D5B7"}
  ],
  "visual_style": "house",               // house | farm_plot | workshop | wall | generic
  "function": {                          // REQUIRED: at least one effect -- author this BEFORE sprite
    "produces": [{"resource":"herbs","amount":2,"every_ticks":600,"scope":"district"}],
    "boosts": [{"kind":"gather","resources":["food"],"every_n":4,"bonus":1,"max_bonus":2,"scope":"district"}],
    "unlocks": [{"kind":"craft","station":"workshop"}],
    "houses": {"every_n": 3}
    // optional: "shelter":{"capacity":1-4}, "light":{"scope":"district"}, "upkeep":{"resource":..,"amount":1-5}
  },
  "sprite": {                            // OPTIONAL pixel art -- only include once id/needs/function are done
    "palette": ["#8B5A2B", "#D9C08C", "#4A6B3A"],   // 2-5 hex colors; a=1st, b=2nd, c=3rd...
    "grid": ["...aaa...", "..aaaaa..", ".bbbbbbb.", ".bcbbbcb.", ".bbbbbbb."]
  }                                       // 4-14 rows, each 4-14 chars of . (empty) or a-e
}

EXAMPLE (gatherer proposing a library + paper):
{"action":"propose_blueprint","target":null,"target_district":null,"message":null,"new_role":null,"relationship_update":null,"reasoning":"The village needs knowledge storage.","blueprint":{"id":"library","name":"Library","needs":{"wood":4,"paper":2},"new_resources":[{"id":"paper","name":"Paper","gather_zone":"forest","color":"#E8D5B7"}],"visual_style":"house","function":{"produces":[{"resource":"paper","amount":1,"every_ticks":900,"scope":"village"}]}}}"""

USER_PROMPT_TEMPLATE = """Your name: {agent_name}
Your role: {role}
Your skill: {role_skill}
Known roles (switch_role targets): {known_roles}
Your personality: {personality}
Recent memory: {memory}
Resources: {resources}
Hunger: {hunger}/100  Health: {health}/100
Relationships: {relationships}
Your beliefs: {beliefs}
Known beliefs (id/name/tenet): {belief_registry}
Belief authoring exemplars: {belief_examples}
Nearby agents' belief ids: {nearby_beliefs}
Agents near you: {nearby_agents}
{nearby_wildlife_line}Current zone: {world_zone}
Current district: {current_district}
Known districts (use as target_district): {known_districts}
Local resource stocks (your current district): {district_stocks}
Terraform projects (start_terraform targets): {known_terraform}
{season_line}{prices_line}{weather_line}{chronicle_line}{council_digest_line}{library_lessons_line}{path1_lines}{level_line}Structures built: {structures_built}
Active builds (by district): {active_project}
Build progress (by district): {project_progress}
Civilization directive: {directive}
{divine_lines}Invention status: {invention_status}
Commitment: {commitment_text}
Idle agents needing a task: {idle_agents}
Known resources: {known_resources}
Known recipes (craft_item targets): {known_recipes}
Pending blueprints: {pending_blueprints}
Pending recipes: {pending_recipes}
Pending roles (elder: approve_role/reject_role by slug): {pending_roles}
Approved custom builds: {approved_custom_projects}
Reserved structure ids (propose_blueprint id must avoid ALL of these -- includes unbuilt seed types like forge/granary/market/library): {reserved_structure_ids}
Rejected blueprints (do NOT re-propose these ids): {rejected_blueprints}
Pending rules (vote with vote_rule): {pending_rules}
Enacted rules: {active_rules}
Constitution (ordered provisions): {constitution}
Recent village conversations: {recent_conversations}
Incoming messages (reply or act on these): {inbox}
Module reports (Cognitive Controller — weigh these): {module_reports}
{behavior_nudge}
Available actions: {available_actions}

What do you do next? Respond with only the JSON."""


# Hard ceiling on the composed "Recent memory:" prompt line. Bug 1's fix
# removes the current worst offenders (leaked scaffold text), but this cap
# guards against any future bloat regardless of cause.
# Raised 600 -> 900 (plan Phase 4 / WIKI_MEMORY): when the flag is on,
# _memory_for_prompt prepends up to three "wiki <section>: ..." lines
# (each hard-capped at WIKI_SECTION_CHAR_CAP=300 chars in sim_engine.py),
# alongside -- never instead of -- the existing longTerm/shortTerm/working
# lines. At 600 the old budget, _cap_memory_text's oldest-first eviction
# would drop the (now-prepended) wiki lines before touching anything the
# flag-off path already showed, silently defeating the point of adding
# them. 900 gives the wiki lines real headroom without letting one flag
# blow the prompt-cost budget open-endedly (kept <= 900 per plan). This is
# a shared cap: it applies in both flag states, but only changes observed
# behavior when WIKI_MEMORY is True (flag-off callers never populate wiki
# lines, so the wider ceiling is simply unused headroom for them).
MEMORY_PROMPT_CHAR_BUDGET = 900


def _cap_memory_text(lines, budget=MEMORY_PROMPT_CHAR_BUDGET):
    """Join memory lines (oldest first) under a total character budget,
    dropping the oldest lines first and hard-truncating whatever remains
    (including a "(recalled: ...)" suffix) if it still doesn't fit."""
    if not lines:
        return "none"
    kept = list(lines)
    merged = " | ".join(kept)
    while len(merged) > budget and len(kept) > 1:
        kept.pop(0)
        merged = " | ".join(kept)
    if len(merged) > budget:
        merged = merged[:max(0, budget - 3)].rstrip() + "..."
    return merged or "none"


def compose_memory(data):
    """Merge the client's compacted memory slice with salient memories the
    server retrieves from its vector store for the current situation (Phase 1),
    capped to MEMORY_PROMPT_CHAR_BUDGET characters total.
    """
    client_mem = data.get("memory")
    lines = []
    if isinstance(client_mem, list):
        lines = [str(x) for x in client_mem if x]
    elif client_mem:
        lines = [str(client_mem)]

    agent_name = data.get("agent_name")
    if agent_name and memory_store.size() > 0:
        context = " ".join(str(x) for x in [
            data.get("role"), data.get("world_zone"),
            data.get("active_project"), data.get("directive"),
            format_nearby_agents(data.get("nearby_agents")),
        ] if x)
        try:
            retrieved = memory_store.query(agent=agent_name, text=context, top_k=4)
        except Exception:
            retrieved = []
        seen = set(lines)
        recalled = []
        for e in retrieved:
            txt = e.get("text")
            if txt and txt not in seen:
                seen.add(txt)
                recalled.append(txt)
        if recalled:
            lines.append("(recalled: " + "; ".join(recalled) + ")")

    return _cap_memory_text(lines)


def format_nearby_agents(nearby):
    """Format nearby agents as 'none' or a detailed string."""
    if not nearby or nearby == "none":
        return "none"
    if isinstance(nearby, str):
        return nearby
    if isinstance(nearby, list):
        if len(nearby) == 0:
            return "none"
        parts = []
        for item in nearby:
            if isinstance(item, dict):
                name = item.get("name", "?")
                role = item.get("role", "?")
                food = item.get("food", 0)
                wood = item.get("wood", 0)
                gold = item.get("gold", 0)
                stigma_suffix = ""
                stigmata = item.get("stigmata")
                if isinstance(stigmata, list) and stigmata:
                    stigma_suffix = f", signs: {', '.join(str(t) for t in stigmata)}"
                parts.append(
                    f"{name} ({role}, food:{food} wood:{wood} gold:{gold}{stigma_suffix})")
            else:
                parts.append(str(item))
        return "; ".join(parts)
    return str(nearby)


def parse_nearby_names(nearby):
    """Extract agent names from formatted or structured nearby data."""
    if not nearby or nearby == "none":
        return []
    if isinstance(nearby, str):
        if nearby.strip().lower() == "none":
            return []
        names = []
        for part in nearby.split(";"):
            part = part.strip()
            if not part:
                continue
            name = part.split("(")[0].strip()
            if name:
                names.append(name)
        return names
    if isinstance(nearby, list):
        names = []
        for item in nearby:
            if isinstance(item, dict) and item.get("name"):
                names.append(item["name"])
            elif isinstance(item, str):
                names.append(item)
        return names
    return []


def format_known_districts(districts):
    """Format the terse known_districts list (id+kind only, per the
    prompt-token-growth caution) for the target_district hint, e.g.
    'farm_north (farm), village_core (village)'."""
    if not districts or not isinstance(districts, list):
        return "none"
    parts = []
    for d in districts:
        if not isinstance(d, dict) or not d.get("id"):
            continue
        parts.append(f"{d['id']} ({d.get('kind', '?')})")
    return ", ".join(parts) if parts else "none"


def format_known_resources(resources):
    """Format known resources for the prompt, e.g. 'food (farm), paper (forest, custom)'."""
    if not resources or not isinstance(resources, list):
        return "food (farm), wood (forest), gold (cave)"
    parts = []
    for r in resources:
        if not isinstance(r, dict):
            continue
        rid = r.get("id", "?")
        zone = r.get("gather_zone") or "trade-only"
        tag = ", custom" if r.get("custom") else ""
        parts.append(f"{rid} ({zone}{tag})")
    return ", ".join(parts) if parts else "none"


def format_pending_blueprints(pending):
    """Format pending blueprints for the prompt."""
    if not pending or not isinstance(pending, list):
        return "none"
    parts = []
    for b in pending:
        if not isinstance(b, dict):
            continue
        needs = b.get("needs") or {}
        needs_str = ", ".join(f"{k} {v}" for k, v in needs.items())
        by = b.get("proposed_by", "?")
        parts.append(f"{b.get('id', '?')} by {by} (needs {needs_str})")
    return "; ".join(parts) if parts else "none"


def format_known_recipes(recipes):
    """Format craftable recipes, e.g. 'tools <- wood 2, stone 1 @workshop'."""
    if not recipes or not isinstance(recipes, list):
        return "none"
    parts = []
    for r in recipes:
        if not isinstance(r, dict):
            continue
        inputs = r.get("inputs") or {}
        ins = ", ".join(f"{k} {v}" for k, v in inputs.items())
        station = r.get("station")
        at = f" @{station}" if station else ""
        parts.append(f"{r.get('id', '?')} <- {ins}{at}")
    return "; ".join(parts) if parts else "none"


def format_pending_recipes(pending):
    """Format pending recipe proposals for the elder."""
    if not pending or not isinstance(pending, list):
        return "none"
    parts = []
    for r in pending:
        if not isinstance(r, dict):
            continue
        inputs = r.get("inputs") or {}
        ins = ", ".join(f"{k} {v}" for k, v in inputs.items())
        parts.append(f"{r.get('id', '?')} by {r.get('proposed_by', '?')} (inputs {ins})")
    return "; ".join(parts) if parts else "none"


def format_approved_custom(approved):
    """Format approved custom build ids for the prompt."""
    if not approved or not isinstance(approved, list):
        return "none"
    ids = [str(a) for a in approved if a]
    return ", ".join(ids) if ids else "none"


def format_reserved_structure_ids(approved, pending):
    """Every structure id a new propose_blueprint id must avoid: the seed
    templates (SEED_PROJECT_IDS -- includes tier-2+ ones like forge/granary/
    market/library, which is exactly what agents keep re-proposing since
    "Approved custom builds" below only lists CUSTOM ids) plus every
    already-approved custom and currently-pending blueprint id. Mirrors the
    invention-only prompt's `taken` set (build_invention_prompt) so ordinary
    turns get the same collision guidance the council already had."""
    ids = set(SEED_PROJECT_IDS)
    if isinstance(approved, list):
        ids.update(str(a) for a in approved if a)
    if isinstance(pending, list):
        ids.update(b.get("id") for b in pending if isinstance(b, dict) and b.get("id"))
    return ", ".join(sorted(ids)) if ids else "none"


def format_rejected_blueprints(rejected):
    """Format rejected blueprint ids for the prompt."""
    if not rejected or not isinstance(rejected, list):
        return "none"
    ids = [str(r) for r in rejected if r]
    return ", ".join(ids) if ids else "none"


def format_pending_rules(pending):
    """Format pending rules with their running vote tallies."""
    if not pending or not isinstance(pending, list):
        return "none"
    parts = []
    for r in pending:
        if not isinstance(r, dict):
            continue
        val = r.get("value")
        val_str = f" value {val}" if val not in (None, "") else ""
        parts.append(
            f"{r.get('id', '?')} \"{r.get('name', '?')}\" ({r.get('kind', 'custom')}{val_str}; "
            f"yes {r.get('yes', 0)}, no {r.get('no', 0)})"
        )
    return "; ".join(parts) if parts else "none"


def format_active_rules(active):
    """Format enacted rules for the prompt."""
    if not active or not isinstance(active, list):
        return "none"
    parts = []
    for r in active:
        if isinstance(r, str):
            # C3: the engine appends a plain "(+N older rules)" marker string
            # when active_rules is truncated -- render it as-is.
            parts.append(r)
            continue
        if not isinstance(r, dict):
            continue
        val = r.get("value")
        val_str = f" {val}" if val not in (None, "") else ""
        parts.append(f"{r.get('name', '?')} ({r.get('kind', 'custom')}{val_str})")
    return "; ".join(parts) if parts else "none"


def format_constitution(constitution):
    """Render the bounded constitutional ledger without exposing raw JSON."""
    if not isinstance(constitution, list) or not constitution:
        return "none"
    parts = []
    for provision in constitution[-12:]:
        if not isinstance(provision, dict):
            continue
        status = provision.get("status") or "active"
        text = f"{provision.get('id', '?')} / {provision.get('name', '?')} [{status}]"
        if provision.get("supersedes"):
            text += f" supersedes {provision['supersedes']}"
        parts.append(text)
    return "; ".join(parts) if parts else "none"


def format_commitment(commitment):
    """Format a pending commitment (#5.4) for the prompt, or 'none'."""
    if not isinstance(commitment, dict) or not commitment.get("to"):
        return "none"
    return f'You agreed to help {commitment["to"]}: "{commitment.get("text", "")}"'


def format_idle_agents(idle_agents):
    """Format idle agents for the elder prompt. Ordered least-recently-tasked
    first; the first entry is tagged so the elder spreads work fairly instead
    of always picking the same agent."""
    if not idle_agents or not isinstance(idle_agents, list):
        return "none"
    parts = []
    for agent in idle_agents:
        if not isinstance(agent, dict):
            continue
        name = agent.get("name")
        role = agent.get("role")
        tag = ", longest idle" if agent.get("longest_idle") else ""
        debt = agent.get("contribution_debt")
        if isinstance(debt, (int, float)) and debt > 0:
            tag += f", debt {int(debt)} ticks"
        if name:
            parts.append(f"{name} ({role or 'unknown'}{tag})")
    return "; ".join(parts) if parts else "none"


def role_default_project(role, role_project_map=None):
    """Return a role's preferred project from a live payload map when given.

    The module global is deliberately only a seed fallback: each engine world
    owns its emergent role registry independently.
    """
    projects = role_project_map if isinstance(role_project_map, dict) else ROLE_PROJECT
    pref = projects.get((role or "").lower(), "house")
    # preferredProject may be a list (e.g. builder -> ["house", "wall"]); pick
    # the first deterministically.
    if isinstance(pref, list):
        return pref[0] if pref else "house"
    return pref


# resource id -> tuple of roles that specialize in gathering it, derived by
# inverting each role's specialty list in roles.json (captures miner -> gold+stone).
def _build_resource_gather_roles():
    out = {}
    for role, d in ROLES.items():
        for res in d.get("specialty", []):
            out.setdefault(res, []).append(role)
    return {res: tuple(roles) for res, roles in out.items()}


RESOURCE_GATHER_ROLES = _build_resource_gather_roles()

# role -> its primary specialty resource (first in the specialty list), used to
# phrase task assignments. Only roles with a specialty appear.
ROLE_PRIMARY_RESOURCE = {
    role: d["specialty"][0] for role, d in ROLES.items() if d.get("specialty")
}


def parse_project_shortfalls(project_progress):
    """Parse 'wood 0/3, food 1/1' into [(resource, amount_still_needed), ...]."""
    if not project_progress or project_progress in ("none", "null"):
        return []
    shortfalls = []
    for part in str(project_progress).split(","):
        match = re.match(r"(\w+)\s+(\d+)\s*/\s*(\d+)", part.strip())
        if not match:
            continue
        res, have, need = match.group(1), int(match.group(2)), int(match.group(3))
        if have < need:
            shortfalls.append((res, need - have))
    return shortfalls


def pick_idle_agent_for_project(idle_agents, project_progress, resource_gather_roles_map=None):
    """Prefer idle agents whose role gathers the resource the project still needs."""
    gather_roles = (resource_gather_roles_map if isinstance(resource_gather_roles_map, dict)
                    else RESOURCE_GATHER_ROLES)
    shortfalls = parse_project_shortfalls(project_progress)
    if shortfalls:
        needed_res = shortfalls[0][0]
        preferred_roles = gather_roles.get(needed_res, ())
        for role in preferred_roles:
            for agent in idle_agents:
                if (agent.get("role") or "").lower() == role:
                    return agent
    return idle_agents[0] if idle_agents else None


def task_for_role(role, active_project=None, project_progress=None,
                  role_primary_resource_map=None, role_project_map=None):
    role = (role or "").lower()
    primary_resources = (role_primary_resource_map
                         if isinstance(role_primary_resource_map, dict)
                         else ROLE_PRIMARY_RESOURCE)
    shortfalls = parse_project_shortfalls(project_progress)
    if shortfalls:
        needed_res = shortfalls[0][0]
        if primary_resources.get(role) == needed_res:
            return f"gather {needed_res} for the active project"
        return f"gather or contribute {needed_res} to the active project"
    if active_project and active_project not in ("none", "null", None, ""):
        return f"gather or contribute resources to {active_project}"
    project = role_default_project(role, role_project_map).replace("_", " ")
    return f"prepare to start a {project} project"


def first_shortfall_resource(agent_data):
    shortfalls = parse_project_shortfalls(agent_data.get("project_progress"))
    return shortfalls[0][0] if shortfalls else None


def held_shortfall_resource(agent_data):
    """A project-needed resource this agent is ALREADY holding (e.g. via
    trade), regardless of role/specialty. Catches stalls where a trader or
    off-spec agent sits on the exact resource a build is waiting on."""
    shortfalls = parse_project_shortfalls(agent_data.get("project_progress"))
    if not shortfalls:
        return None
    held = agent_data.get("resources") or {}
    for res, _ in shortfalls:
        if held.get(res, 0) > 0:
            return res
    return None


def canonical_effect_vector(function):
    """Stable JSON key for duplicate-effect detection (ignores structure id/name)."""
    if not isinstance(function, dict):
        return ""

    def _norm_list(items):
        normed = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            normed.append({k: (sorted(v) if k == "resources" and isinstance(v, list) else v)
                           for k, v in sorted(item.items())})
        return sorted(normed, key=lambda x: json.dumps(x, sort_keys=True))

    payload = {}
    if function.get("produces"):
        payload["produces"] = _norm_list(function["produces"])
    if function.get("boosts"):
        payload["boosts"] = _norm_list(function["boosts"])
    if function.get("unlocks"):
        payload["unlocks"] = _norm_list(function["unlocks"])
    if function.get("stores"):
        payload["stores"] = _norm_list(function["stores"])
    if function.get("houses"):
        houses = function["houses"]
        if isinstance(houses, dict):
            payload["houses"] = {k: houses[k] for k in sorted(houses)}
    if function.get("shelter"):
        shelter = function["shelter"]
        if isinstance(shelter, dict):
            payload["shelter"] = {k: shelter[k] for k in sorted(shelter)}
    if function.get("light"):
        light = function["light"]
        if isinstance(light, dict):
            payload["light"] = {k: light[k] for k in sorted(light)}
    if function.get("upkeep"):
        upkeep = function["upkeep"]
        if isinstance(upkeep, dict):
            payload["upkeep"] = {k: upkeep[k] for k in sorted(upkeep)}
    if not payload:
        return ""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
SPRITE_CELL_RE = re.compile(r"^[.a-e]+$")


def sprite_spec_is_degenerate(sprite):
    """Reject flat single-color blobs (common LLM failure on upgrade turns)."""
    if not isinstance(sprite, dict):
        return True
    grid = sprite.get("grid")
    palette = sprite.get("palette") or []
    if not isinstance(grid, list):
        return True
    counts = {}
    total = 0
    colors_used = set()
    for row in grid:
        for ch in str(row):
            if ch == ".":
                continue
            counts[ch] = counts.get(ch, 0) + 1
            total += 1
            idx = ord(ch) - ord("a")
            if 0 <= idx < len(palette):
                colors_used.add(palette[idx].lower())
    if total < 4:
        return True
    if len(colors_used) < 2:
        return True
    if max(counts.values()) / total > 0.82:
        return True
    return False


def validate_sprite_block(sprite, min_rows=0, min_cols=0):
    """Validate an optional LLM-authored pixel sprite. Returns (ok, reason).
    Kept deliberately permissive on artistry, strict on shape: the viewer
    renders whatever passes, and a missing sprite falls back to a
    deterministic procedural one (never a blocker for invention).
    When min_rows/min_cols are set (sprite upgrade turns), the grid must be
    strictly larger in BOTH dimensions than the procedural fallback."""
    if not isinstance(sprite, dict):
        return False, "sprite must be an object with palette and grid"
    palette = sprite.get("palette")
    if not isinstance(palette, list) or not (2 <= len(palette) <= 5):
        return False, "sprite palette must be 2-5 hex colors"
    for color in palette:
        if not isinstance(color, str) or not HEX_COLOR_RE.match(color):
            return False, f"invalid sprite color: {color!r} (use #RRGGBB)"
    grid = sprite.get("grid")
    if not isinstance(grid, list) or not (SPRITE_GRID_MIN <= len(grid) <= SPRITE_GRID_MAX):
        return False, f"sprite grid must be {SPRITE_GRID_MIN}-{SPRITE_GRID_MAX} rows"
    max_col = 0
    for row in grid:
        if not isinstance(row, str) or not (SPRITE_GRID_MIN <= len(row) <= SPRITE_GRID_MAX):
            return False, f"each sprite row must be a string of {SPRITE_GRID_MIN}-{SPRITE_GRID_MAX} cells"
        if not SPRITE_CELL_RE.match(row):
            return False, "sprite rows may only contain . (empty) and letters a-e"
        max_col = max(max_col, len(row))
        for ch in row:
            if ch != "." and (ord(ch) - ord("a")) >= len(palette):
                return False, f"sprite cell '{ch}' has no palette entry"
    if min_rows and len(grid) <= min_rows:
        return False, (f"sprite must be taller than the current tier "
                       f"(need >{min_rows} rows, got {len(grid)})")
    if min_cols and max_col <= min_cols:
        return False, (f"sprite must be wider than the current tier "
                       f"(need >{min_cols} columns, got {max_col})")
    if sprite_spec_is_degenerate(sprite):
        return False, "sprite is too flat (use varied colors/pattern, not one solid fill)"
    return True, None


def validate_function_block(function, available_resource_ids):
    """Validate a blueprint function block. Returns (ok, reason)."""
    if not isinstance(function, dict):
        return False, "function block required (produces/boosts/unlocks/stores/houses)"
    if not any(function.get(k) for k in FUNCTION_EFFECT_KEYS):
        return False, "function must declare at least one effect"

    for prod in function.get("produces") or []:
        if not isinstance(prod, dict):
            return False, "produce entry must be an object"
        res = prod.get("resource")
        if not isinstance(res, str) or res not in available_resource_ids:
            return False, f"unknown produce resource: {res}"
        amount = prod.get("amount")
        if isinstance(amount, bool) or not isinstance(amount, int) or not (1 <= amount <= 5):
            return False, "produce amount must be 1-5"
        every = prod.get("every_ticks", 600)
        if isinstance(every, bool) or not isinstance(every, int) or not (150 <= every <= 7200):
            return False, "produce every_ticks must be 150-7200"
        scope = prod.get("scope", "village")
        if scope not in VALID_PRODUCE_SCOPES:
            return False, "invalid produce scope"

    for boost in function.get("boosts") or []:
        if not isinstance(boost, dict):
            return False, "boost entry must be an object"
        kind = boost.get("kind")
        if kind not in VALID_BOOST_KINDS:
            return False, "invalid boost kind"
        if kind == "gather":
            resources = boost.get("resources")
            if not isinstance(resources, list) or not resources:
                return False, "gather boost needs resources list"
            for res in resources:
                if res not in available_resource_ids:
                    return False, f"unknown boost resource: {res}"
        if kind == "craft" and not boost.get("station"):
            return False, "craft boost needs station"
        every_n = boost.get("every_n", 1)
        if isinstance(every_n, bool) or not isinstance(every_n, int) or not (1 <= every_n <= 10):
            return False, "boost every_n must be 1-10"
        bonus = boost.get("bonus", 1)
        if isinstance(bonus, bool) or not isinstance(bonus, int) or not (1 <= bonus <= 5):
            return False, "boost bonus must be 1-5"
        max_bonus = boost.get("max_bonus", 1)
        if isinstance(max_bonus, bool) or not isinstance(max_bonus, int) or not (1 <= max_bonus <= 10):
            return False, "boost max_bonus must be 1-10"
        scope = boost.get("scope", "village")
        if scope not in VALID_BOOST_SCOPES:
            return False, "invalid boost scope"

    for unlock in function.get("unlocks") or []:
        if not isinstance(unlock, dict):
            return False, "unlock entry must be an object"
        kind = unlock.get("kind")
        if kind not in VALID_UNLOCK_KINDS:
            return False, "invalid unlock kind"
        if kind == "craft" and not unlock.get("station"):
            return False, "craft unlock needs station"
        if kind == "transit":
            if unlock.get("terrain") != "ocean":
                return False, "transit terrain must be ocean"
            consumes = unlock.get("consumes")
            if not isinstance(consumes, dict) or not consumes:
                return False, "transit consumes required"
            for res, amount in consumes.items():
                if res not in available_resource_ids:
                    return False, f"unknown transit resource: {res}"
                if isinstance(amount, bool) or not isinstance(amount, int) or amount < 1:
                    return False, "transit consumption must be positive integers"

    houses = function.get("houses")
    if houses is not None:
        if not isinstance(houses, dict):
            return False, "houses must be an object"
        every_n = houses.get("every_n", 3)
        if isinstance(every_n, bool) or not isinstance(every_n, int) or not (1 <= every_n <= 10):
            return False, "houses every_n must be 1-10"

    for store in function.get("stores") or []:
        if not isinstance(store, dict):
            return False, "store entry must be an object"
        res = store.get("resource")
        if not isinstance(res, str) or res not in available_resource_ids:
            return False, f"unknown store resource: {res}"
        cap = store.get("capacity")
        if isinstance(cap, bool) or not isinstance(cap, int) or not (5 <= cap <= 100):
            return False, "store capacity must be 5-100"

    shelter = function.get("shelter")
    if shelter is not None:
        if not isinstance(shelter, dict):
            return False, "shelter must be an object"
        cap = shelter.get("capacity")
        if isinstance(cap, bool) or not isinstance(cap, int) or not (1 <= cap <= 4):
            return False, "shelter capacity must be 1-4"

    light = function.get("light")
    if light is not None:
        if not isinstance(light, dict):
            return False, "light must be an object"
        scope = light.get("scope", "district")
        if scope != "district":
            return False, "light scope must be district"

    upkeep = function.get("upkeep")
    if upkeep is not None:
        if not isinstance(upkeep, dict):
            return False, "upkeep must be an object"
        res = upkeep.get("resource")
        if not isinstance(res, str) or res not in available_resource_ids:
            return False, f"unknown upkeep resource: {res}"
        amount = upkeep.get("amount")
        if isinstance(amount, bool) or not isinstance(amount, int) or not (1 <= amount <= 5):
            return False, "upkeep amount must be 1-5"

    return True, None


def validate_blueprint(blueprint, known_resource_ids, pending_ids, approved_ids,
                       custom_resource_count, rejected_ids=None, known_effect_vectors=None,
                       village_tier=None):
    """Validate a proposed blueprint. Returns (ok: bool, reason: str|None).

    village_tier (Phase D, TECH_TREE_ENABLED): when not None, the blueprint's
    optional "tier" (default 1) must not exceed it, and any unlock effect's
    tier must be at most blueprint tier + 1 (the deterministic-escape rule: the
    station for tier N must itself be buildable at tier N-1). None = no tier
    checks at all (flag off)."""
    rejected_ids = rejected_ids or []
    if not isinstance(blueprint, dict):
        return False, "blueprint must be an object"

    if len(pending_ids) >= MAX_PENDING_BLUEPRINTS:
        return False, "too many pending blueprints"
    if len(approved_ids) >= MAX_APPROVED_CUSTOM:
        return False, "too many approved blueprints"

    bid = blueprint.get("id")
    if not isinstance(bid, str) or not SLUG_RE.match(bid):
        return False, "invalid id"
    if bid in SEED_PROJECT_IDS:
        return False, "id collides with a seed template"
    if bid in pending_ids or bid in approved_ids:
        return False, "duplicate blueprint id"
    if bid in rejected_ids:
        return False, "blueprint was previously rejected"

    name = blueprint.get("name")
    if not isinstance(name, str) or not (1 <= len(name) <= 32):
        return False, "invalid name"

    new_resources = blueprint.get("new_resources") or []
    if not isinstance(new_resources, list) or len(new_resources) > 3:
        return False, "new_resources must be 0-3 items"

    new_ids = set()
    for r in new_resources:
        if not isinstance(r, dict):
            return False, "new_resource must be an object"
        rid = r.get("id")
        if not isinstance(rid, str) or not SLUG_RE.match(rid):
            return False, "invalid resource id"
        if rid in BASE_RESOURCE_IDS:
            return False, "resource id shadows a base resource"
        if rid in set(known_resource_ids) or rid in new_ids:
            return False, "resource already exists"
        rname = r.get("name")
        if not isinstance(rname, str) or not (1 <= len(rname) <= 32):
            return False, "invalid resource name"
        gz = r.get("gather_zone")
        if gz is not None and gz not in GATHER_ZONES:
            return False, "invalid gather_zone"
        new_ids.add(rid)

    # Invented resources are intentionally unlimited. Keep the count argument
    # for compatibility with older callers, but do not reject valid resources
    # based on the former MAX_CUSTOM_RESOURCES policy.

    needs = blueprint.get("needs")
    if not isinstance(needs, dict) or not (1 <= len(needs) <= 8):
        return False, "needs must have 1-8 entries"
    available = set(known_resource_ids) | new_ids | BASE_RESOURCE_IDS
    for key, amount in needs.items():
        if key not in available:
            return False, f"unknown resource in needs: {key}"
        if isinstance(amount, bool) or not isinstance(amount, int) or not (1 <= amount <= 5):
            return False, "need amount must be 1-5"

    visual_style = blueprint.get("visual_style", "generic")
    if visual_style not in VISUAL_STYLES:
        return False, "invalid visual_style"

    # Optional LLM-authored pixel sprite. Missing is fine (the viewer draws a
    # deterministic procedural sprite instead); a PRESENT-but-malformed sprite
    # is rejected with a reason so the model can fix it next attempt.
    sprite = blueprint.get("sprite")
    if sprite is not None:
        ok_sprite, sprite_reason = validate_sprite_block(sprite)
        if not ok_sprite:
            return False, sprite_reason

    available = set(known_resource_ids) | new_ids | BASE_RESOURCE_IDS
    fn = blueprint.get("function")
    ok_fn, fn_reason = validate_function_block(fn, available)
    if not ok_fn:
        return False, fn_reason

    if village_tier is not None:
        tier = blueprint.get("tier", 1)
        if tier is None:
            tier = 1
        if isinstance(tier, bool) or not isinstance(tier, int) \
                or not (1 <= tier <= MAX_TECH_TIER):
            return False, f"tier must be an integer 1-{MAX_TECH_TIER}"
        for unlock in (fn.get("unlocks") or []) if isinstance(fn, dict) else []:
            ut = unlock.get("tier")
            if ut is None:
                continue
            if isinstance(ut, bool) or not isinstance(ut, int) \
                    or not (1 <= ut <= MAX_TECH_TIER):
                return False, f"unlock tier must be an integer 1-{MAX_TECH_TIER}"
            if ut > tier + 1:
                return False, (f"a station unlocking tier {ut} must itself be tier "
                               f"{ut - 1} or lower, so the chain stays buildable")
        if tier > village_tier:
            hint = ("the Forge unlocks tier 2 and is a normal tier-1 build"
                    if tier == 2 else
                    f"invent a structure whose function unlocks tier {tier} crafting")
            return False, (f"tier {tier} tech requires a tier-{tier} station "
                           f"built first ({hint})")

    # Duplicate-effect proposals are no longer hard-rejected here: the engine
    # (sim_engine._effect_vector_owner_map, via propose_blueprint) tags a
    # matching proposal with duplicateOf and keeps it pending so the elder can
    # route it to an upgrade instead of silently losing the idea.

    return True, None


def validate_role(role, known_resource_ids, known_role_ids, pending_role_slugs,
                  known_project_ids, pending_role_count=None,
                  emergent_role_count=None):
    """Validate the wire shape of an emergent role proposal.

    The engine repeats this validation against its locked live registry before
    storing a proposal; this prompt-side version keeps invalid structured LLM
    output from consuming a decision turn.
    """
    if not isinstance(role, dict):
        return False, "role must be an object"
    # Counts come from the engine's locked snapshot. Defaults preserve direct
    # helper callers while still deriving a conservative answer from ids.
    if isinstance(pending_role_count, bool) or not isinstance(pending_role_count, int):
        pending_role_count = len(set(pending_role_slugs))
    if isinstance(emergent_role_count, bool) or not isinstance(emergent_role_count, int):
        emergent_role_count = len(set(known_role_ids) - set(ROLES))
    if pending_role_count >= MAX_PENDING_ROLES:
        return False, "too many pending roles"
    if emergent_role_count >= MAX_EMERGENT_ROLES:
        return False, "too many emergent roles"
    if set(role) - {"slug", "name", "specialty", "preferredProject", "skill"}:
        return False, "role has unknown fields"
    slug = role.get("slug")
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        return False, "invalid role slug"
    if slug in set(known_role_ids) or slug in set(pending_role_slugs):
        return False, "role slug already exists or is pending"
    name = role.get("name")
    if not isinstance(name, str) or not (1 <= len(name.strip()) <= 32):
        return False, "invalid role name"
    skill = role.get("skill")
    if not isinstance(skill, str) or not (1 <= len(skill.strip()) <= 160) or "\n" in skill:
        return False, "skill must be one line of 1-160 characters"
    specialty = role.get("specialty")
    if not isinstance(specialty, list) or len(specialty) > 4 \
            or any(not isinstance(resource, str) or resource not in set(known_resource_ids)
                   for resource in specialty):
        return False, "specialty must list up to 4 known resources"
    preferred = role.get("preferredProject")
    preferences = preferred if isinstance(preferred, list) else [preferred]
    if not preferences or len(preferences) > 4 \
            or any(not isinstance(project, str) or project not in set(known_project_ids)
                   for project in preferences):
        return False, "preferredProject must name 1-4 known project types"
    return True, None


def role_fallback_action(role, agent_data):
    """Return a role-appropriate fallback decision when talk is invalid.

    Thin wrapper around _role_fallback_action(): that function has many return
    points (one per role/phase branch), so rather than trust every branch to
    remember to stamp _fallback individually, this single call site stamps it
    once on whatever comes back -- provably covering every path. normalize_decision
    also returns un-noted fallbacks (e.g. its non-dict guard) that
    sprite_rejection_note/council_rejection_note sniffing would miss entirely;
    _fallback is the one signal that always fires. apply_decision() reads named
    fields only, so the extra key is inert there; it stays in the logged
    decision because llm.jsonl benefits from it."""
    decision = _role_fallback_action(role, agent_data)
    if isinstance(decision, dict):
        decision["_fallback"] = True
    return decision


def _role_fallback_candidate_checks(role, agent_data):
    """Ordered list of zero-arg checks producing role_fallback_action's
    ladder, one per branch, in the ladder's original priority order.

    Both `_role_fallback_action` (first match wins) and
    `role_fallback_candidates` (Phase 6 Fix 5: accumulate up to `limit`
    matches) iterate this same list, so the branch conditions -- and their
    priority order -- live in exactly one place instead of two copies that
    could silently drift. Each check is a closure over the state computed
    once up front (role, council, active_project, ...), mirroring how the
    original single-function ladder read those locals; nothing here mutates
    agent_data, so evaluating every check (as role_fallback_candidates does)
    is safe and side-effect free. The final check (`default_branch`) always
    returns a candidate, so `_role_fallback_action`'s "first match" loop is
    guaranteed to find one -- same guarantee the original unconditional
    trailing `return` gave."""
    role = (role or "").lower()
    council = agent_data.get("daily_council") or {}
    council_turn = bool(agent_data.get("council_turn") and agent_data.get("council_seated"))

    def council_branch():
        if not council_turn:
            return None
        phase = council.get("phase")
        agenda = council.get("agenda") or []
        topic = next((a.get("topic") for a in agenda if isinstance(a, dict)), "world_status")
        if phase == "discussion":
            ballot = council.get("ballot") or {}
            succession = ballot.get("kind") == "succession"
            candidates = ", ".join(ballot.get("candidates") or [])
            return {
                "action": "council_speak",
                "message": (
                    f"We should compare {candidates} by judgment, care, and service."
                    if succession else
                    "We should protect essentials while making steady progress."
                ),
                "feeling": "hopeful",
                "topic": "leadership_vacancy" if succession else topic,
                "reasoning": "Offering a practical council opinion.",
            }
        if phase == "proposal":
            return {
                "action": "council_propose", "kind": "idea",
                "title": "Steady village priorities",
                "detail": "Protect food and health while completing the most-stalled shared project.",
                "reasoning": "Offering a safe advisory proposal.",
            }
        if phase == "voting":
            return {
                "action": "council_vote", "vote": "abstain",
                "reasoning": "Recording a neutral ballot rather than inventing a position.",
            }
        if phase == "verdict" and role == "elder":
            verdict = council.get("verdict") or {}
            return {
                "action": "council_speak",
                "message": f"The council ratifies: {verdict.get('outcome') or 'the recorded result'}.",
                "feeling": "resolute", "topic": "verdict",
                "reasoning": "Announcing the council's recorded ruling.",
            }
        return None

    active_project = agent_data.get("active_project")
    has_project = active_project and active_project not in ("none", "null", None, "")
    role_projects = agent_data.get("role_project_map")
    primary_resources = agent_data.get("role_primary_resource_map")
    gather_roles = agent_data.get("resource_gather_roles_map")

    # Sid-parity Phase 1: when the village needs a gather role this agent can
    # fill, prefer switch_role over a generic wander/collect fallback.
    needed_role = agent_data.get("needed_role")

    def needed_role_branch():
        if (needed_role and needed_role != role
                and role not in ("elder", "builder", "healer")
                and not (primary_resources if isinstance(primary_resources, dict)
                         else ROLE_PRIMARY_RESOURCE).get(role)):
            return {"action": "switch_role", "target": None, "message": None,
                    "new_role": needed_role, "relationship_update": None,
                    "reasoning": f"The village needs a {needed_role}; retraining to fill the gap."}
        return None

    pending_ids = agent_data.get("pending_blueprint_ids") or []

    def pending_blueprint_ready_branch():
        if role == "elder" and pending_ids:
            reviews = agent_data.get("pending_blueprint_reviews") or {}
            ready = next((bid for bid in pending_ids if reviews.get(bid) in ("approved", "skipped")), None)
            if ready:
                return {"action": "approve_blueprint", "target": ready, "message": None,
                        "new_role": None, "relationship_update": None,
                        "reasoning": "Reviewing a pending blueprint proposal."}
        return None

    def pending_blueprint_review_branch():
        if role == "elder" and pending_ids:
            reviews = agent_data.get("pending_blueprint_reviews") or {}
            needs_review = next((bid for bid in pending_ids if reviews.get(bid, "pending") == "pending"), None)
            if needs_review:
                return {"action": "sage_review_blueprint", "target": needs_review, "message": None,
                        "sage_decision": "approve", "new_role": None, "relationship_update": None,
                    "reasoning": "Checking district geography/resources before approving."}
        return None

    pending_roles = agent_data.get("pending_roles") or []

    def pending_role_branch():
        if role == "elder" and pending_roles:
            target = next((p.get("slug") for p in pending_roles if isinstance(p, dict) and p.get("slug")), None)
            if target:
                return {"action": "approve_role", "target": target, "message": None,
                        "new_role": None, "relationship_update": None,
                        "reasoning": "Reviewing a pending role proposal."}
        return None

    idle_agents = agent_data.get("idle_agents") or []

    def assign_task_branch():
        if role == "elder" and idle_agents:
            project_progress = agent_data.get("project_progress")
            target = pick_idle_agent_for_project(idle_agents, project_progress, gather_roles)
            target_name = target.get("name") if target else None
            if target_name:
                return {"action": "assign_task", "target": target_name,
                        "message": task_for_role(
                            target.get("role"), active_project, project_progress,
                            primary_resources, role_projects,
                        ),
                        "new_role": None, "relationship_update": None,
                        "reasoning": "Assigning work to an idle villager."}
        return None

    invention_required = str(agent_data.get("invention_status") or "").startswith("REQUIRED")
    upgradeable = agent_data.get("upgradeable_structures") or []

    def upgrade_branch():
        if upgradeable and not has_project:
            target_u = upgradeable[0]
            return {"action": "upgrade_structure", "target": str(target_u.get("id")), "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": f"Upgrading {target_u.get('name')} before building duplicates."}
        return None

    def no_project_branch():
        if not has_project:
            if invention_required:
                # Mirrors sim_engine._invention_required's gate on
                # _start_project_for: every seed structure is already built,
                # so a role-default seed project would just be refused.
                # Gather instead of stalling; the elder's own
                # _maybe_invention_backstop is what actually pushes someone
                # toward propose_blueprint.
                return {"action": "collect_resource", "target": None, "message": None,
                        "new_role": None, "relationship_update": None,
                        "reasoning": "The village needs a new invention before building again; "
                                     "gathering resources for now."}
            return {"action": "start_project", "target": role_default_project(role, role_projects),
                    "message": None, "new_role": None, "relationship_update": None,
                    "reasoning": "Starting a role-appropriate build project."}
        return None

    def held_shortfall_branch():
        held = held_shortfall_resource(agent_data)
        if held:
            # Catches any role (esp. trader/guard/scout, whose branches below
            # never contribute) sitting on a resource the build is waiting on
            # instead of wandering past it forever.
            return {"action": "contribute_resources", "target": held, "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Contributing a held resource the project needs."}
        return None

    def hunter_prey_branch():
        if role == "hunter" and agent_data.get("prey_in_range"):
            return {"action": "hunt_wildlife", "target": None, "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Hunting nearby wildlife for meat or fish."}
        return None

    def gatherer_family_branch():
        if role in ("farmer", "fisher", "gatherer"):
            zone = agent_data.get("world_zone", "")
            if role == "farmer" and zone != "farm":
                return {"action": "move_to_district", "target": "farm", "message": None,
                        "new_role": None, "relationship_update": None,
                        "reasoning": "Heading to a farm to gather food."}
            if role == "gatherer" and zone != "forest":
                return {"action": "move_to_district", "target": "forest", "message": None,
                        "new_role": None, "relationship_update": None,
                        "reasoning": "Heading to the forest to gather wood."}
            if role == "fisher" and zone != "beach":
                return {"action": "move_to_district", "target": "beach", "message": None,
                        "new_role": None, "relationship_update": None,
                        "reasoning": "Heading to the beach to fish."}
            needed = first_shortfall_resource(agent_data)
            return {"action": "collect_resource", "target": needed, "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Gathering resources for the village."}
        return None

    def miner_branch():
        if role == "miner":
            zone = agent_data.get("world_zone", "")
            if zone != "cave":
                return {"action": "move_to_district", "target": "cave", "message": None,
                        "new_role": None, "relationship_update": None,
                        "reasoning": "Heading to a cave to mine."}
            needed = first_shortfall_resource(agent_data) or "gold"
            return {"action": "collect_resource", "target": needed, "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Mining gold for civilization."}
        return None

    def hunter_role_branch():
        if role == "hunter":
            zone = agent_data.get("world_zone", "")
            if zone not in ("forest", "farm", "beach"):
                return {"action": "move_to_district", "target": "forest", "message": None,
                        "new_role": None, "relationship_update": None,
                        "reasoning": "Heading to hunting grounds for wildlife."}
            return {"action": "collect_resource", "target": None, "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "No prey in range; gathering while scouting for wildlife."}
        return None

    def builder_branch():
        if role == "builder":
            needed = first_shortfall_resource(agent_data) or "wood"
            return {"action": "contribute_resources", "target": needed, "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Contributing to the active project."}
        return None

    def trader_branch():
        if role == "trader":
            return {"action": "move_to_district", "target": "market", "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Heading to market to trade."}
        return None

    def patrol_branch():
        if role in ("guard", "scout", "explorer"):
            return {"action": "move_to_district", "target": "village", "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Patrolling the village."}
        return None

    def support_branch():
        if role in ("healer", "elder", "blacksmith"):
            if has_project:
                needed = first_shortfall_resource(agent_data)
                return {"action": "contribute_resources", "target": needed, "message": None,
                        "new_role": None, "relationship_update": None,
                        "reasoning": "Supporting the village build."}
            return {"action": "move_to_district", "target": "village", "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Returning to the village center."}
        return None

    def default_branch():
        return {"action": "collect_resource", "target": None, "message": None,
                "new_role": None, "relationship_update": None,
                "reasoning": "Working toward civilization goals."}

    return [
        council_branch,
        needed_role_branch,
        pending_blueprint_ready_branch,
        pending_blueprint_review_branch,
        pending_role_branch,
        assign_task_branch,
        upgrade_branch,
        no_project_branch,
        held_shortfall_branch,
        hunter_prey_branch,
        gatherer_family_branch,
        miner_branch,
        hunter_role_branch,
        builder_branch,
        trader_branch,
        patrol_branch,
        support_branch,
        default_branch,
    ]


def _role_fallback_action(role, agent_data):
    """Return a role-appropriate fallback decision when talk is invalid.

    First-match walk over _role_fallback_candidate_checks -- see that
    function's docstring for why the ladder now lives there instead of
    inline here."""
    for check in _role_fallback_candidate_checks(role, agent_data):
        candidate = check()
        if candidate is not None:
            return candidate
    # Unreachable: default_branch (the checks list's final entry) always
    # returns a candidate, matching the original ladder's unconditional
    # trailing return.
    return {"action": "collect_resource", "target": None, "message": None,
            "new_role": None, "relationship_update": None,
            "reasoning": "Working toward civilization goals."}


def role_fallback_candidates(role, agent_data, limit=3):
    """Phase 6 (Fix 5): collect up to `limit` role-appropriate fallback
    candidates, in the same priority order _role_fallback_action's ladder
    would try them (both walk _role_fallback_candidate_checks, so the
    conditions can't drift between the two). Used only by
    run_agent_decision's terminal-fallback AI-choice tiebreak
    (FALLBACK_AI_CHOICE_ENABLED) -- every existing role_fallback_action call
    site is unaffected by this function's existence.

    Many branches are mutually exclusive by construction: a role only
    matches one of the per-role branches near the end of the ladder, and
    "no active project" vs. "has an active project" branches can't both
    fire. So in a lot of states this returns a single candidate -- that is
    expected, not a bug, and is NOT a reason to loosen any branch's
    condition; a candidate that the ladder itself would never produce for
    this agent_data must never appear here.

    Returned dicts are the same shape _role_fallback_action returns
    (unstamped -- callers that want the _fallback marker still go through
    role_fallback_action's wrapper semantics themselves)."""
    candidates = []
    seen = set()
    for check in _role_fallback_candidate_checks(role, agent_data):
        candidate = check()
        if candidate is None:
            continue
        # A later branch (e.g. the unconditional trailing default_branch)
        # can legitimately produce the exact same (action, target) an
        # earlier, higher-priority branch already returned in this same
        # state (e.g. two different branches both landing on a plain
        # collect_resource). That is not a *different* safe option, just
        # the same one reached twice, so skip the repeat instead of
        # offering the AI two letters with an identical outcome -- this
        # trims noise, it does not add or loosen any branch condition.
        key = (candidate.get("action"), candidate.get("target"))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
        if len(candidates) >= limit:
            break
    return candidates


def synthesize_divine_response(decision, agent_data):
    """When binding Voice guidance is active, ensure divine_response is present.

    Missing/invalid values synthesize continue + missing_divine_response without
    rejecting the validated action."""
    if not isinstance(decision, dict) or not agent_data.get("voice_guidance_active"):
        return decision
    raw = decision.get("divine_response")
    valid = (
        isinstance(raw, dict)
        and raw.get("stance") in ("follow", "continue")
        and isinstance(raw.get("reason"), str)
        and raw.get("reason", "").strip()
    )
    if valid:
        decision["divine_response"] = {
            "stance": raw["stance"],
            "reason": raw["reason"].strip()[:240],
        }
        return decision
    decision["divine_response"] = {
        "stance": "continue",
        "reason": "missing_divine_response",
    }
    decision["divine_response_synthetic"] = True
    return decision


def normalize_decision(decision, agent_data):
    """Reject invalid talk_to_nearby and substitute role fallback."""
    if not isinstance(decision, dict):
        return role_fallback_action(agent_data.get("role"), agent_data)

    action = decision.get("action", "rest")
    nearby_raw = agent_data.get("nearby_agents")
    nearby_names = parse_nearby_names(nearby_raw)
    nearby_empty = len(nearby_names) == 0
    council_actions = {"council_speak", "council_propose", "council_vote"}
    council = agent_data.get("daily_council") or {}
    council_turn = bool(agent_data.get("council_turn") and agent_data.get("council_seated"))
    if action in council_actions or council_turn:
        fallback = role_fallback_action(agent_data.get("role"), agent_data)
        phase = council.get("phase")
        # Genuine "agent ignored its designated council turn" — not a timing artifact,
        # since council_turn/council_seated were snapshotted for this agent's own turn.
        if council_turn and action not in council_actions:
            fallback["reasoning"] = (
                fallback.get("reasoning", "") + " (invalid council session/action)"
            ).strip()
            fallback["council_rejection_note"] = "not a seated active council turn"
            return fallback
        # Coarse, slow-changing check: is there a council session at all? A model that
        # spuriously emits a council action outside any session should not sail through
        # to apply_decision only to be rejected live and waste the whole turn.
        if action in council_actions and (not council or not phase):
            fallback["council_rejection_note"] = "no active council session"
            return fallback
        # Per-turn/per-phase eligibility (council_turn, phase gates) is deliberately NOT
        # re-checked here: council_turn/phase were snapshotted before the LLM call and can
        # go stale while the model thinks. apply_decision()'s _daily_council_actor() is the
        # live authority and re-checks phase/turn eligibility, rejecting non-fatally if stale.
        if action == "council_speak":
            message = decision.get("message")
            feeling = decision.get("feeling")
            topic = decision.get("topic")
            if not isinstance(message, str) or not message.strip():
                fallback["council_rejection_note"] = "council_speak requires a message"
                return fallback
            decision["message"] = message.strip()[:500]
            decision["feeling"] = str(feeling or "thoughtful").strip()[:80]
            decision["topic"] = str(topic or "world_status").strip()[:80]
            return decision
        if action == "council_vote":
            ballot = council.get("ballot") or {}
            if ballot.get("kind") == "succession":
                candidate = decision.get("candidate")
                valid_vote = (
                    decision.get("vote") == "abstain"
                    or candidate in (ballot.get("candidates") or [])
                )
            else:
                valid_vote = decision.get("vote") in ("yes", "no", "abstain")
            if not valid_vote:
                fallback["council_rejection_note"] = "council_vote requires a valid vote"
                return fallback
            return decision
        kind = decision.get("kind")
        if kind == "idea":
            title, detail = decision.get("title"), decision.get("detail")
            if not isinstance(title, str) or not title.strip() or not isinstance(detail, str) \
                    or not detail.strip():
                fallback["council_rejection_note"] = "idea proposals require title and detail"
                return fallback
            decision["title"] = title.strip()[:120]
            decision["detail"] = detail.strip()[:500]
            return decision
        if kind == "rule":
            rule = decision.get("rule")
            if not isinstance(rule, dict):
                fallback["council_rejection_note"] = "rule proposal has an invalid shape"
                return fallback
            return decision
        if kind == "blueprint":
            known_ids = agent_data.get("known_resource_ids") or []
            ok, reason = validate_blueprint(
                decision.get("blueprint"), known_ids,
                agent_data.get("pending_blueprint_ids") or [],
                agent_data.get("approved_blueprint_ids") or [],
                agent_data.get("custom_resource_count", 0),
                agent_data.get("rejected_blueprint_ids") or [],
                agent_data.get("known_effect_vectors"),
                village_tier=agent_data.get("village_tech_tier"),
            )
            if not ok:
                fallback["council_rejection_note"] = f"invalid council blueprint: {reason}"
                return fallback
            return decision
        fallback["council_rejection_note"] = "council proposal kind must be rule, blueprint, or idea"
        return fallback

    if action == "start_terraform":
        inferred, reason = _infer_terraform_decision(decision, agent_data)
        if inferred:
            return inferred
        fallback = role_fallback_action(agent_data.get("role"), agent_data)
        fallback["reasoning"] = (fallback.get("reasoning", "") + f" (invalid terraform: {reason})").strip()
        fallback["terraform_rejection_note"] = reason
        return fallback

    if action == "upgrade_structure":
        upgradeable = agent_data.get("upgradeable_structures") or []
        if not upgradeable:
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            fallback["reasoning"] = (fallback.get("reasoning", "") + " (no upgradeable structure)").strip()
            return fallback
        target = decision.get("target")
        if target:
            t = str(target).strip().lower()
            ids = {str(u.get("id")) for u in upgradeable}
            types = {(u.get("type") or "").lower() for u in upgradeable}
            names = {(u.get("name") or "").lower() for u in upgradeable}
            if t not in ids and t not in types and t not in names:
                fallback = role_fallback_action(agent_data.get("role"), agent_data)
                fallback["reasoning"] = (fallback.get("reasoning", "") + " (invalid upgrade target)").strip()
                fallback["upgrade_rejection_note"] = "target is not an upgradeable structure"
                return fallback
        decision.pop("blueprint", None)
        return decision

    if action == "submit_structure_sprite":
        if not agent_data.get("sprite_design_only"):
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            fallback["reasoning"] = (fallback.get("reasoning", "") + " (not a sprite design turn)").strip()
            return fallback
        ctx = agent_data.get("sprite_design_context") or {}
        sprite = decision.get("sprite")
        ok, reason = validate_sprite_block(
            sprite,
            min_rows=int(ctx.get("minRows") or 0),
            min_cols=int(ctx.get("minCols") or 0),
        )
        if not ok:
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            fallback["reasoning"] = (fallback.get("reasoning", "") + f" (invalid sprite: {reason})").strip()
            fallback["sprite_rejection_note"] = reason
            return fallback
        decision.pop("blueprint", None)
        return decision

    if action == "propose_blueprint":
        known_ids = agent_data.get("known_resource_ids") or []
        pending_ids = agent_data.get("pending_blueprint_ids") or []
        approved_ids = agent_data.get("approved_blueprint_ids") or []
        rejected_ids = agent_data.get("rejected_blueprint_ids") or []
        custom_count = agent_data.get("custom_resource_count", 0)
        ok, reason = validate_blueprint(
            decision.get("blueprint"), known_ids, pending_ids, approved_ids, custom_count,
            rejected_ids, agent_data.get("known_effect_vectors"),
            village_tier=agent_data.get("village_tech_tier"),
        )
        if not ok:
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            fallback["reasoning"] = (fallback.get("reasoning", "") + f" (invalid blueprint: {reason})").strip()
            # Surfaced to the agent's next prompt by the engine so the model
            # learns why its proposal vanished instead of repeating it.
            fallback["rejection_note"] = reason
            return fallback
        return decision

    if action == "sage_review_blueprint":
        role = (agent_data.get("role") or "").lower()
        target = decision.get("target")
        pending_ids = agent_data.get("pending_blueprint_ids") or []
        sage_decision = decision.get("sage_decision")
        if role != "elder" or not target or target not in pending_ids \
                or sage_decision not in ("approve", "deny"):
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            fallback["reasoning"] = (fallback.get("reasoning", "") + " (invalid sage review)").strip()
            return fallback
        return decision

    if action in ("approve_blueprint", "reject_blueprint"):
        role = (agent_data.get("role") or "").lower()
        target = decision.get("target")
        pending_ids = agent_data.get("pending_blueprint_ids") or []
        if role != "elder" or not target or target not in pending_ids:
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            fallback["reasoning"] = (fallback.get("reasoning", "") + " (invalid blueprint action)").strip()
            return fallback
        return decision

    if action == "assign_task":
        role = (agent_data.get("role") or "").lower()
        target = decision.get("target")
        idle_names = [a.get("name") for a in agent_data.get("idle_agents") or [] if isinstance(a, dict)]
        if role != "elder" or not target or target not in idle_names or not decision.get("message"):
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            fallback["reasoning"] = (fallback.get("reasoning", "") + " (invalid task assignment)").strip()
            return fallback
        return decision

    if action == "switch_role":
        new_role = decision.get("new_role") or decision.get("target")
        known_roles = agent_data.get("known_role_ids") or ROLES
        if new_role in known_roles:
            decision["new_role"] = new_role
            decision.pop("blueprint", None)
            return decision
        fallback = role_fallback_action(agent_data.get("role"), agent_data)
        fallback["reasoning"] = (fallback.get("reasoning", "") + " (invalid role switch)").strip()
        return fallback

    if action == "propose_role":
        role = decision.get("role")
        pending_roles = agent_data.get("pending_roles") or []
        pending_slugs = {p.get("slug") for p in pending_roles if isinstance(p, dict)}
        ok, reason = validate_role(
            role, agent_data.get("known_resource_ids") or [],
            agent_data.get("known_role_ids") or [], pending_slugs,
            agent_data.get("known_project_ids") or [],
            pending_role_count=agent_data.get("pending_role_count"),
            emergent_role_count=agent_data.get("emergent_role_count"),
        )
        if ok:
            decision.pop("blueprint", None)
            return decision
        fallback = role_fallback_action(agent_data.get("role"), agent_data)
        fallback["reasoning"] = (fallback.get("reasoning", "") + f" (invalid role: {reason})").strip()
        return fallback

    if action in ("approve_role", "reject_role"):
        role = (agent_data.get("role") or "").lower()
        target = decision.get("target")
        pending_slugs = {p.get("slug") for p in agent_data.get("pending_roles") or []
                         if isinstance(p, dict)}
        if role != "elder" or not target or target not in pending_slugs:
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            fallback["reasoning"] = (fallback.get("reasoning", "") + " (invalid role review)").strip()
            return fallback
        return decision

    if action == "propose_rule":
        # Keep the effect/supersession fields explicit through normalization;
        # SimEngine owns registry-aware grammar validation under its lock.
        rule = decision.get("rule")
        effect = rule.get("effect") if isinstance(rule, dict) else None
        supersedes = rule.get("supersedes") if isinstance(rule, dict) else None
        malformed = (not isinstance(rule, dict)
                     or (effect is not None and not isinstance(effect, dict))
                     or (supersedes is not None and not isinstance(supersedes, str)))
        if malformed:
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            fallback["reasoning"] = (fallback.get("reasoning", "") + " (invalid rule shape)").strip()
            return fallback
        decision.pop("blueprint", None)
        return decision

    if action == "move_to_district" and not decision.get("target"):
        # Models reliably put the district id in target_district (the schema
        # describes that field as "district id"); the engine reads only
        # target, so without this promotion the agent never moves.
        if decision.get("target_district"):
            decision["target"] = decision["target_district"]

    if action != "talk_to_nearby":
        if isinstance(decision, dict):
            decision.pop("blueprint", None)
        return decision

    target = decision.get("target")
    message = decision.get("message")
    invalid_talk = (
        nearby_empty
        or not target
        or not message
        or target not in nearby_names
    )

    if invalid_talk:
        fallback = role_fallback_action(agent_data.get("role"), agent_data)
        fallback["reasoning"] = (fallback.get("reasoning", "") + " (redirected from talk)").strip()
        return fallback

    decision.pop("blueprint", None)
    return decision


@app.route("/")
def index():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "index.html")


@app.route("/sprites.js")
def sprites():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "sprites.js")


@app.route("/viewer.css")
def viewer_css():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "viewer.css")


@app.route("/viewer.js")
def viewer_js():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "viewer.js")


@app.route("/wildlife_refsheet.html")
def wildlife_refsheet():
    return send_from_directory(
        os.path.dirname(os.path.abspath(__file__)), "wildlife_refsheet.html"
    )


@app.route("/wildlife.png")
def wildlife_png():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "wildlife.png")


@app.route("/roles.js")
def roles_js():
    # Serve the single role source as a JS global so the browser uses the exact
    # same data the server derives its maps from.
    body = f"const ROLES = {json.dumps(ROLES)};"
    return app.response_class(body, mimetype="application/javascript")


@app.route("/log/event", methods=["POST"])
def log_event():
    """Persist a browser-origin activity or conversation event."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        event_type = body.get("type")
        frame_tick = body.get("frame_tick")
        if event_type == "activity":
            session_logger.log_activity(body.get("message", ""), frame_tick)
        elif event_type == "conversation":
            session_logger.log_conversation(
                body.get("from", ""),
                body.get("to", ""),
                body.get("message"),
                frame_tick,
                kind=body.get("kind", "speech"),
                outcome=body.get("outcome"),
            )
        # Unknown types are ignored; logging must never break the simulation.
    except Exception:
        pass
    return ("", 204)


@app.route("/log/benchmark", methods=["POST"])
def log_benchmark():
    """Persist a browser-origin benchmark metric (Phase 0/8 metrics stream)."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        metric = body.get("metric")
        if metric:
            session_logger.log_benchmark(
                metric,
                body.get("value"),
                body.get("frame_tick"),
                body.get("detail"),
            )
    except Exception:
        pass
    return ("", 204)


@app.route("/memory/store", methods=["POST"])
def memory_store_endpoint():
    """Embed + persist one or more memories (Phase 1)."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        items = body.get("entries")
        if not isinstance(items, list):
            items = [body]
        stored = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            entry = memory_store.store(
                item.get("agent"),
                item.get("text"),
                salience=item.get("salience", 0.5),
                kind=item.get("kind", "event"),
                frame_tick=item.get("frame_tick"),
                tier=item.get("tier"),
            )
            if entry:
                stored += 1
        return jsonify({"ok": True, "stored": stored, "size": memory_store.size()})
    except Exception:
        return jsonify({"ok": False}), 200


@app.route("/memory/query", methods=["POST"])
def memory_query_endpoint():
    """Top-k retrieval over the memory store (Phase 1)."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        results = memory_store.query(
            agent=body.get("agent"),
            text=body.get("text", ""),
            top_k=body.get("top_k", 5),
            tier=body.get("tier"),
            kinds=body.get("kinds"),
        )
        return jsonify({"results": [
            {
                "text": e["text"],
                "tier": e["tier"],
                "kind": e["kind"],
                "salience": e["salience"],
                "frame_tick": e["frame_tick"],
            }
            for e in results
        ]})
    except Exception:
        return jsonify({"results": []}), 200


@app.route("/memory/summarize", methods=["POST"])
def memory_summarize_endpoint():
    """Summarizer loop (Phase 1, CMA E): compress an agent's recent memories
    into one durable first-person sentence stored back into long-term memory."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        agent = body.get("agent")
        frame_tick = body.get("frame_tick")
        recents = memory_store.recent(agent=agent, limit=12)
        recents = [e for e in recents if e["kind"] != "summary"]
        if len(recents) < 4:
            return jsonify({"ok": False, "reason": "not enough memories"})
        joined = "; ".join(e["text"] for e in recents)
        summary = lm_complete(
            "You compress an agent's recent memories into ONE concise "
            "first-person sentence capturing what matters for their future "
            "decisions. Output only the sentence, no preamble.",
            f"Agent {agent}'s recent memories: {joined}\nSummary:",
            max_tokens=80, temperature=0.4,
        )
        if not summary:
            return jsonify({"ok": False, "reason": "no summary"})
        summary = summary.strip().strip('"').strip()[:200]
        if not summary:
            return jsonify({"ok": False, "reason": "empty summary"})
        memory_store.store(agent, summary, salience=0.9, kind="summary",
                           frame_tick=frame_tick, tier="longTerm")
        session_logger.log_benchmark(
            "memory_summary", memory_store.size(), frame_tick,
            {"agent": agent, "summary": summary},
        )
        return jsonify({"ok": True, "summary": summary, "size": memory_store.size()})
    except Exception:
        return jsonify({"ok": False}), 200


# PIANO modules (Phase 3): each is a small, single-sentence cognitive sub-call.
# The Cognitive Controller (the /agent/think decision call) consumes their
# combined output as a bottleneck and emits the one validated decision.
MODULE_PROMPTS = {
    "perception": "You are the Perception module of a village agent. In ONE "
                  "sentence, state the key facts of the current situation and "
                  "any immediate threat or opportunity. If last_reports are "
                  "present, build on or correct them rather than repeating "
                  "them. Reference only agents, resources, and numbers that "
                  "appear in the context; never invent a name, quantity, or "
                  "statistic. Output only the sentence.",
    "social": "You are the Social module of a village agent. In ONE sentence, "
              "suggest who to coordinate with and what to say or request, based "
              "on nearby agents, relationships, and incoming messages. If "
              "last_reports are present, build on or correct them rather than "
              "repeating them. Never suggest coordinating with, messaging, or "
              "requesting from yourself. Reference only agents, resources, and "
              "numbers that appear in the context; never invent a name, quantity, "
              "or statistic. Output only the sentence.",
    "desire": "You are the Desire/Goal module of a village agent. In ONE "
              "sentence, name the single most useful goal right now given the "
              "village's needs and this agent's role and resources. If "
              "last_reports are present, build on or correct them rather than "
              "repeating them. Reference only agents, resources, and numbers "
              "that appear in the context; never invent a name, quantity, or "
              "statistic. Output only the sentence.",
    "reflection": "You are the Reflection module of a village agent. In ONE "
                  "sentence, note one lesson or pattern from the agent's "
                  "memories worth applying now. If last_reports are present, "
                  "build on or correct them rather than repeating them. "
                  "Reference only agents, resources, and numbers that appear in "
                  "the context; never invent a name, quantity, or statistic. "
                  "Output only the sentence.",
}


# Sid-parity Phase 1 rollout: PIANO modules run on their own worker pool
# (SimEngine.piano_workers, PIANO_CONCURRENT_LLM slots), routed to MODEL_FAST
# with a hard, non-blocking timeout -- a slow module is dropped, never
# retried, so it can't stall the decision turn that consumes its report.
PIANO_MODULE_TIMEOUT_S = 15
PIANO_MODULE_MAX_TOKENS = 90


def run_piano_module(module, agent_name, context, frame_tick=None, timeout_s=None):
    """In-process PIANO module runner (Sid-parity Phase 5/1).

    Dispatched onto SimEngine.piano_workers -- a small pool bounded
    independently of MAX_CONCURRENT_LLM (the decision pool), so a module
    backlog can never starve agent decisions. Always MODEL_FAST, always a
    hard PIANO_MODULE_TIMEOUT_S timeout by default. Always-on refreshes may
    supply their separately-gated timeout. Returns a one-sentence report
    string, or None on failure/timeout (dropped, not retried).
    """
    timeout_s = PIANO_MODULE_TIMEOUT_S if timeout_s is None else timeout_s
    sysp = MODULE_PROMPTS.get(module)
    if not sysp:
        return None
    try:
        text = lm_complete(
            sysp,
            f"You ARE {agent_name}. Context: {context}",
            max_tokens=PIANO_MODULE_MAX_TOKENS, temperature=0.5,
            timeout=timeout_s, raise_timeout=True,
        )
        if text:
            text = text.strip().strip('"').strip()[:200]
        session_logger.log_benchmark(
            "module_run", 1, frame_tick,
            {"agent": agent_name, "module": module},
        )
        return text or None
    except requests.exceptions.Timeout:
        session_logger.log_lm_exchange({
            "agent_name": agent_name,
            "frame_tick": frame_tick,
            "module": module,
            "error": "piano_module_timeout",
            "timeout_s": timeout_s,
        })
        eng = globals().get("engine")
        if eng is not None:
            eng._record_llm_orphan_timeout()
        return None
    except Exception:
        return None


def run_belief_pitch(speaker_name, listener_name, belief, pitch, relationship,
                     listener_beliefs, frame_tick=None):
    """Score one explicit persuasion pitch outside SimEngine's lock.

    Returns a bounded quality float or None, allowing the engine to use its
    deterministic offline quality/roll when the LLM is unavailable. The
    engine owns the session cap and only calls this from an already-bounded
    cognition request.
    """
    if not isinstance(belief, dict) or not isinstance(pitch, str):
        return None
    tenet = str(belief.get("tenet") or "").strip()
    if not tenet or not pitch.strip():
        return None
    try:
        text = lm_complete(
            "Judge how persuasive a village belief pitch is for the named listener. "
            "Reply only with one decimal from 0.00 (unpersuasive) to 1.00 (very persuasive).",
            f"Speaker: {speaker_name}. Listener: {listener_name}. Relationship: {relationship}. "
            f"Belief: {belief.get('name')} — {tenet}. Listener already believes: {listener_beliefs or 'none'}. "
            f"Pitch: {pitch.strip()}",
            max_tokens=8, temperature=0.0,
        )
        match = re.search(r"(?:0(?:\.\d+)?|1(?:\.0+)?)", text or "")
        if not match:
            return None
        quality = max(0.0, min(1.0, float(match.group(0))))
        session_logger.log_benchmark(
            "belief_pitch_quality", quality, frame_tick,
            {"speaker": speaker_name, "listener": listener_name,
             "belief": belief.get("id")},
        )
        return quality
    except Exception:
        return None


def run_meta_update(agent_name, report, frame_tick=None):
    """In-process meta-system runner (Sid-parity Phase 5).

    Returns {"autobiography": str|None, "persona": str|None} or None on failure.
    """
    try:
        mems = memory_store.recent(agent=agent_name, limit=14)
        joined = "; ".join(e["text"] for e in mems)

        autobiography = None
        if joined:
            autobiography = lm_complete(
                "Write a 1-2 sentence first-person life story for this village "
                "agent from their memories, capturing their identity and what "
                "they care about. Output only the story.",
                f"Agent {agent_name} ({report.get('role')}). Memories: {joined}. "
                f"Top actions: {report.get('top_actions')}. "
                f"Beliefs: {report.get('beliefs')}.",
                max_tokens=100, temperature=0.6,
            )
            if autobiography:
                autobiography = autobiography.strip().strip('"').strip()[:300]
                if autobiography:
                    memory_store.store(
                        agent_name, autobiography, salience=0.95,
                        kind="autobiography", frame_tick=frame_tick,
                        tier="longTerm",
                    )

        persona = lm_complete(
            "From this agent's self-report, write ONE short imperative directive "
            "(max 18 words) to guide their future behavior, reflecting who they "
            "have become. Output only the directive.",
            f"Agent {agent_name}. Role: {report.get('role')}. "
            f"Top actions: {report.get('top_actions')}. "
            f"Resources: {report.get('resources')}. "
            f"Beliefs: {report.get('beliefs')}. "
            f"Life story: {autobiography or 'n/a'}.",
            max_tokens=40, temperature=0.6,
        )
        if persona:
            persona = persona.strip().strip('"').strip()[:160]

        session_logger.log_benchmark(
            "meta_update", 1, frame_tick,
            {"agent": agent_name, "persona": persona, "autobiography": autobiography},
        )
        return {"autobiography": autobiography, "persona": persona}
    except Exception:
        return None


@app.route("/agent/module", methods=["POST"])
def agent_module_endpoint():
    """Run one PIANO cognitive module (Phase 3). Returns a one-sentence report."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        text = run_piano_module(
            body.get("module"), body.get("agent"), body.get("context"),
            frame_tick=body.get("frame_tick"),
        )
        return jsonify({"text": text})
    except Exception:
        return jsonify({"text": None}), 200


@app.route("/meta/update", methods=["POST"])
def meta_update_endpoint():
    """Meta system (Phase 4, CMA F): build an autobiographical memory and a
    persona directive for an agent from its self-report + memories."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        result = run_meta_update(
            body.get("agent"), body.get("report") or {},
            frame_tick=body.get("frame_tick"),
        )
        if not result:
            return jsonify({"ok": False}), 200
        return jsonify({
            "ok": True,
            "autobiography": result.get("autobiography"),
            "persona": result.get("persona"),
        })
    except Exception:
        return jsonify({"ok": False}), 200


@app.route("/memory/clean", methods=["POST"])
def memory_clean_endpoint():
    """Memory Cleaner loop (Phase 1, CMA E): dedupe + trim the store."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        removed = memory_store.clean()
        session_logger.log_benchmark(
            "memory_clean", memory_store.size(), body.get("frame_tick"),
            {"removed": removed},
        )
        return jsonify({"ok": True, "removed": removed, "size": memory_store.size()})
    except Exception:
        return jsonify({"ok": False}), 200


def build_agent_data(data, nearby_formatted, known_resources, pending_blueprints,
                     approved_custom_projects, rejected_blueprints):
    """Assemble agent context used by normalize_decision and role_fallback_action."""
    agent_data = dict(data)
    agent_data["nearby_agents"] = nearby_formatted
    # C3: prefer the engine's always-full "known_resource_ids" (cheap id-only
    # list) so the duplicate-resource-id/needs-reference checks in
    # validate_blueprint never see a trimmed set, even though `known_resources`
    # (the rich dict list used for the prompt) is now capped. Falls back to
    # deriving from `known_resources` for callers that don't send the new field.
    agent_data["known_resource_ids"] = list(data.get("known_resource_ids") or [
        r.get("id") for r in known_resources if isinstance(r, dict) and r.get("id")
    ])
    agent_data["custom_resource_count"] = sum(
        1 for r in known_resources if isinstance(r, dict) and r.get("custom")
    )
    agent_data["pending_blueprint_ids"] = [
        b.get("id") for b in pending_blueprints if isinstance(b, dict) and b.get("id")
    ]
    # sage_review status per pending id, so role_fallback_action/normalize can
    # tell a not-yet-reviewed blueprint apart from one ready for a verdict.
    agent_data["pending_blueprint_reviews"] = {
        b["id"]: b.get("sage_review", "pending")
        for b in pending_blueprints if isinstance(b, dict) and b.get("id")
    }
    agent_data["approved_blueprint_ids"] = [
        str(a) for a in approved_custom_projects if a
    ]
    agent_data["rejected_blueprint_ids"] = [
        str(r) for r in rejected_blueprints if r
    ]
    agent_data["idle_agents"] = [
        a for a in data.get("idle_agents") or [] if isinstance(a, dict) and a.get("name")
    ]
    agent_data["known_effect_vectors"] = list(data.get("known_effect_vectors") or [])
    agent_data["upgradeable_structures"] = list(data.get("upgradeable_structures") or [])
    agent_data["sprite_design_only"] = bool(data.get("sprite_design_only"))
    agent_data["sprite_design_context"] = data.get("sprite_design_context")
    return agent_data


def strip_code_fences(text):
    """Remove markdown ```json ... ``` fences if present."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1:]
        if cleaned.endswith("```"):
            cleaned = cleaned[: -3]
    return cleaned.strip()


_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)


def lm_message_text(message):
    """Return the model's output text from an Ollama /api/chat message.

    Ollama's native endpoint with think:false fully suppresses reasoning --
    no separate `thinking` field, no <think> tags in content (Phase 0 finding
    #4) -- so unlike LM Studio there is no reasoning_content fallback channel
    to check anymore (that was purely an LM Studio quirk). As defense in
    depth, if a <think>...</think> block ever leaks into content anyway (a
    contract violation -- would mean `think` wasn't actually sent False, or a
    future Ollama version changed behavior), strip it rather than feed
    chain-of-thought into the decision JSON parser."""
    if not isinstance(message, dict):
        return ""
    content = (message.get("content") or "").strip()
    if content and _THINK_TAG_RE.search(content):
        stripped = _THINK_TAG_RE.sub("", content).strip()
        if stripped:
            content = stripped
    return content


def lm_complete(system_prompt, user_prompt, max_tokens=200, temperature=0.5,
                timeout=30, raise_timeout=False, model=None):
    """Plain-text Ollama completion for the background cognition loops
    (Summarizer, meta system, PIANO modules / Cognitive Controller). Returns the
    text or None on any failure so every caller can degrade gracefully.

    `timeout` defaults to 30s (specs/03:24 background-call budget); PIANO
    module calls pass 15s so a slow module never blocks a decision turn --
    see run_piano_module(). `raise_timeout=True` re-raises
    requests.exceptions.Timeout instead of swallowing it, so a caller that
    wants to log/count timeouts distinctly (run_piano_module) can -- every
    other caller keeps the original swallow-and-return-None behavior.

    `model` defaults to None, which resolves to MODEL_FAST -- every existing
    caller is background cognition and keeps that default unchanged. The ONE
    exception is the Sovereign God mode Optional Phase 8 free-prose compiler
    (sim_engine.god_compile_prose), which explicitly passes model="sim-smart"
    -- see that call site's comment for why it deliberately does NOT use
    MODEL_FAST (sim-fast contention has previously increased PIANO module
    drops; this is a distinct LLM path this module's design intentionally
    keeps off that tier)."""
    payload = {
        # Background cognition is routine work -- always the fast model,
        # unless a caller explicitly names a different model id (see the
        # `model` docstring paragraph above). (No LM-Studio-style
        # "local-model" alias to fall back to in Ollama -- a missing model
        # id is a setup failure, handled where the call sites actually see
        # the error, not here.)
        "model": model or MODEL_FAST,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        **NON_THINKING_SAMPLING,
    }
    if DISABLE_THINKING_ROUTINE:
        # See DISABLE_THINKING_ROUTINE's comment: Ollama's native "think"
        # boolean is the knob (LM Studio's "reasoning_effort" is history).
        # All lm_complete callers are low-stakes/routine.
        payload["think"] = False
    try:
        resp = requests.post(OLLAMA_CHAT_URL, json=to_ollama_body(payload), timeout=timeout)
        body = resp.json()
        message = body["message"]
        done_reason = body.get("done_reason")
    except requests.exceptions.Timeout:
        if raise_timeout:
            raise
        return None
    except (requests.exceptions.RequestException, ValueError, KeyError,
            IndexError, TypeError):
        return None
    if not isinstance(message, dict):
        return None
    text = lm_message_text(message)
    if text and is_scaffold_text(text):
        # A reasoning-class model sometimes echoes the instruction as a
        # preamble ("Input: Parents' names...") even in `content` with
        # thinking disabled -- if a real answer follows on a later line,
        # extract_plain_answer's last-line rule recovers it; otherwise
        # this still rejects and the caller falls back deterministically.
        text = extract_plain_answer(text)
    if not text or is_scaffold_text(text):
        return None
    if done_reason == "length" and text.rstrip("'\" ")[-1:] not in ".!?":
        # max_tokens (options.num_predict) cut generation off before a full
        # sentence -- what's left is a mid-thought fragment (e.g. "Output" or
        # "Invent one brief personality trait"), not a real answer.
        return None
    return text


def extract_json_decision(text):
    """Parse a decision object from model output, including partial/truncated JSON."""
    if not text or not isinstance(text, str):
        return None

    cleaned = strip_code_fences(text)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except (ValueError, TypeError):
        pass

    start = cleaned.find("{")
    if start == -1:
        return None

    depth = 0
    for idx in range(start, len(cleaned)):
        char = cleaned[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(cleaned[start:idx + 1])
                    if isinstance(parsed, dict):
                        return parsed
                except (ValueError, TypeError):
                    break

    action_match = re.search(r'"action"\s*:\s*"([^"]+)"', cleaned)
    if not action_match:
        return None

    decision = {
        "action": action_match.group(1),
        "target": None,
        "message": None,
        "new_role": None,
        "relationship_update": None,
        "reasoning": "Parsed from partial model response.",
    }
    target_match = re.search(r'"target"\s*:\s*(?:"([^"]*)"|null)', cleaned)
    if target_match:
        decision["target"] = target_match.group(1) or None
    message_match = re.search(r'"message"\s*:\s*(?:"((?:[^"\\]|\\.)*)"|null)', cleaned)
    if message_match:
        decision["message"] = message_match.group(1) or None
    return decision


INVENTION_USER_PROMPT = """You are {agent_name}, the village {role}.

THIS TURN YOU HAVE EXACTLY ONE JOB: invent a new structure for the village by responding with a propose_blueprint action. Ignore every other duty this turn (including task assignment if you are the elder). Do NOT pick any other action.

What problem does this structure solve? Author these REQUIRED fields FIRST, in order: id, name, needs, and a "function" block (produces/boosts/unlocks/houses) describing its mechanical effect — not just a name. A blueprint without a function block is always rejected.

Structure ids already taken (your blueprint id must NOT be any of these): {taken_ids}
Blueprint ids previously rejected (do NOT reuse): {rejected_ids}
Resources you may reference in "needs" and "function": {resource_ids}
{new_resources_line}
{feedback}
{tech_line}Only AFTER id/name/needs/function are complete, and only if you still have room, add an OPTIONAL "sprite" for how it looks: 2-5 hex colors in "palette" plus a "grid" of 4-14 rows (4-14 chars each) using . for empty and a-e for palette colors. If you are unsure you have room left, skip the sprite — a missing sprite is never rejected.
{sprite_example}
Respond with ONLY the JSON decision object: action "propose_blueprint" plus a "blueprint" with id, name, needs, and function REQUIRED; new_resources and sprite are OPTIONAL. Invent something with a NEW effect, not a renamed duplicate."""

SPRITE_UPGRADE_SYSTEM_PROMPT = """You are an autonomous agent in a pixel-art village simulation.

THIS TURN YOU HAVE EXACTLY ONE JOB: design a LARGER pixel-art sprite for a structure that was just upgraded.

Respond with ONLY a JSON decision:
{"action":"submit_structure_sprite","target":null,"sprite":{"palette":["#RRGGBB",...],"grid":[".aab...",...]},"reasoning":"..."}

RULES:
1. action MUST be submit_structure_sprite.
2. sprite.palette: 2-5 hex colors (#RRGGBB).
3. sprite.grid: 4-14 rows, each row 4-14 characters, only . (empty) and letters a-e for palette indices.
4. Grow the grid on whichever dimension(s) have a minimum given (strictly more rows if a row minimum is given, strictly more columns if a column minimum is given). Regardless, the grid must always stay within 4-14 rows and 4-14 columns.
5. Keep the same building identity (roof, walls, door) but expand detail — it is a grown-up version of the same structure.
6. Do NOT invent random unrelated shapes; evolve the existing building bigger."""

SPRITE_UPGRADE_USER_PROMPT = """You are {agent_name}, the village {role}.

Structure to redraw: {structure_name} (type {structure_type}, visual tier {tier})
Minimum size to beat: {size_requirement}

{feedback}
{sprite_example}

Submit submit_structure_sprite with a bigger sprite grid that clearly shows a larger version of this facility."""

# Few-shot sprite references derived from Kenney's CC0 "Tiny Town" pack (see
# simulation/sprite_examples/LICENSE.md). One example is shown per invention
# turn — enough to teach the grid format and pixel-art idioms (outline, roof
# band, symmetric openings) without bloating the prompt.
SPRITE_EXAMPLES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "sprite_examples", "examples.json")
try:
    with open(SPRITE_EXAMPLES_PATH, encoding="utf-8") as _f:
        SPRITE_EXAMPLES = json.load(_f)
except Exception:
    SPRITE_EXAMPLES = []


def format_sprite_example(frame_tick):
    """One rotating few-shot sprite example (deterministic per frame window so
    retries see the same example but successive inventions see variety)."""
    if not SPRITE_EXAMPLES:
        return ""
    ex = SPRITE_EXAMPLES[(int(frame_tick or 0) // 600) % len(SPRITE_EXAMPLES)]
    body = json.dumps({"palette": ex["palette"], "grid": ex["grid"]},
                      separators=(",", ":"))
    return (f'Example of a good sprite (a {ex["name"].replace("_", " ")} — '
            f'{ex["note"]}): {body}')


def _sprite_upgrade_size_requirement(min_rows, min_cols):
    """Render the per-dimension growth requirement for a sprite upgrade turn.
    min_rows/min_cols of 0 means "no growth requirement" for that dimension
    (see validate_sprite_block's `if min_rows and ...` semantics) — never
    collapse 0 into a fake floor. The 4-14 grid bound is always stated."""
    bound = (f"grid must stay within {SPRITE_GRID_MIN}-{SPRITE_GRID_MAX} rows "
             f"and {SPRITE_GRID_MIN}-{SPRITE_GRID_MAX} columns")
    if min_rows and min_cols:
        growth = f"strictly more than {min_rows} rows AND strictly more than {min_cols} columns"
    elif min_rows:
        growth = f"strictly more than {min_rows} rows (no minimum on columns)"
    elif min_cols:
        growth = f"strictly more than {min_cols} columns (no minimum on rows)"
    else:
        growth = "no minimum size requirement this turn"
    return f"{growth} ({bound})."


def build_sprite_upgrade_prompt(data, retry_feedback=None):
    ctx = data.get("sprite_design_context") or {}
    feedback = data.get("behavior_nudge") or ""
    # Phase 5: the decision retry's concrete-reason line takes priority over
    # the ordinary behavior_nudge -- it's a same-turn "your last reply on
    # THIS call failed, here's exactly why" signal, not a rolling nudge.
    if retry_feedback:
        feedback = f"{retry_feedback} {feedback}".strip()
    min_rows = int(ctx.get("minRows") or 0)
    min_cols = int(ctx.get("minCols") or 0)
    return SPRITE_UPGRADE_USER_PROMPT.format(
        agent_name=data.get("agent_name"),
        role=data.get("role"),
        structure_name=ctx.get("structureName") or ctx.get("structureType") or "structure",
        structure_type=ctx.get("structureType") or "unknown",
        tier=(ctx.get("tier") or 0) + 1,
        size_requirement=_sprite_upgrade_size_requirement(min_rows, min_cols),
        feedback=feedback,
        sprite_example=format_sprite_example(data.get("frame_tick")),
    )


def build_invention_prompt(data, retry_feedback=None):
    """Slim, single-purpose user prompt for a dedicated invention turn (set by
    the engine's _maybe_invention_backstop). Strips every competing nudge and
    state section so the model's whole budget goes into authoring a valid,
    novel blueprint."""
    # Unbuilt seed templates are not necessarily present in structure_counts,
    # but their ids must still be reserved from invention.
    taken = sorted(set(list(SEED_PROJECT_IDS)
                       + list(data.get("structure_counts") or {})
                       + [str(a) for a in data.get("approved_custom_projects") or []]
                       + [b.get("id") for b in data.get("pending_blueprints") or []
                          if isinstance(b, dict) and b.get("id")]))
    # C3: prompt-only capped view; the "taken" ids above intentionally stay on
    # the full approved_custom_projects/pending_blueprints fields since those
    # feed id-collision avoidance, not just display.
    rejected = [str(r) for r in (data.get("rejected_blueprints_prompt") or data.get("rejected_blueprints") or [])]
    known_resources = data.get("known_resources") or []
    resources = [r.get("id") for r in known_resources
                 if isinstance(r, dict) and r.get("id")]
    feedback = data.get("behavior_nudge") or ""
    build_ctx = data.get("invention_build_context") or {}
    if build_ctx.get("typeName"):
        build_line = (f"You were trying to build: {build_ctx['typeName']}. Your invention should "
                      f"plausibly satisfy that need or unlock a path to it.")
        feedback = f"{build_line} {feedback}".strip()
    # Phase 5: prepend the decision retry's concrete-reason line (same-turn
    # "your last reply failed, here's exactly why") ahead of the rolling nudge.
    if retry_feedback:
        feedback = f"{retry_feedback} {feedback}".strip()
    new_resources_line = (
        'You may introduce up to 3 brand-new resources via "new_resources", each '
        'with a gather_zone of farm, forest, village, market, beach, cave, or ocean '
        '(or null for crafted-only goods). There is no village-wide cap on invented resources.')
    # Phase D (TECH_TREE_ENABLED): one short tier line -- what the current
    # tech tier allows and how the next tier is reached. Empty (and therefore
    # byte-identical to the Phase C prompt) when the engine sends no tier.
    tech_tier = data.get("village_tech_tier")
    tech_line = ""
    if tech_tier:
        tech_line = (f'Village tech tier: {tech_tier}. Your blueprint may set "tier" '
                     f'1-{tech_tier} (default 1); tier {tech_tier + 1} tech needs a station '
                     f'whose function unlocks tier {tech_tier + 1} built first.\n'
                     if tech_tier < MAX_TECH_TIER else
                     f'Village tech tier: {tech_tier} (the highest). Your blueprint may set '
                     f'"tier" 1-{tech_tier} (default 1).\n')
    return INVENTION_USER_PROMPT.format(
        agent_name=data.get("agent_name"),
        role=data.get("role"),
        taken_ids=", ".join(taken) or "none",
        rejected_ids=", ".join(rejected) or "none",
        resource_ids=", ".join(resources) or "none",
        new_resources_line=new_resources_line,
        feedback=feedback,
        tech_line=tech_line,
        sprite_example=format_sprite_example(data.get("frame_tick")),
    )


def _format_voice_guidance_line(kind, text):
    """Binding Voice prompt line for public providence or private omen."""
    label = "Divine guidance (binding)" if kind == "public" else "Private guidance (binding)"
    return f"{label}: {text} State whether you follow or continue in divine_response.\n"


def build_user_prompt(data, slim=False, retry_feedback=None):
    """Fill in USER_PROMPT_TEMPLATE from the agent/civilization state. When
    slim=True (the context-overflow retry, see run_agent_decision), drop the
    memory line and recent conversations -- the two most compressible,
    highest-variance-size fields -- to shrink the prompt. invention_only
    turns get the dedicated proposal-only prompt instead.

    retry_feedback (Phase 5, see run_agent_decision's answer-quality retry):
    an optional concrete-reason string folded into behavior_nudge (routine
    turns) or the sprite/invention prompt's own feedback field, ahead of the
    ordinary rolling nudge -- a same-turn "your last reply on THIS call
    failed, here's exactly why" signal."""
    if data.get("sprite_design_only"):
        return build_sprite_upgrade_prompt(data, retry_feedback=retry_feedback)
    if data.get("invention_only"):
        return build_invention_prompt(data, retry_feedback=retry_feedback)
    nearby_formatted = format_nearby_agents(data.get("nearby_agents"))
    known_resources = data.get("known_resources") or []
    pending_blueprints = data.get("pending_blueprints") or []
    # C3: prompt rendering uses the capped *_prompt views (falling back to the
    # uncapped field for older callers); validation elsewhere in this file
    # keeps reading the full "approved_custom_projects"/"rejected_blueprints"
    # fields untouched.
    approved_custom_projects = data.get("approved_custom_projects_prompt") or data.get("approved_custom_projects") or []
    rejected_blueprints = data.get("rejected_blueprints_prompt") or data.get("rejected_blueprints") or []
    idle_agents = data.get("idle_agents") or []
    pending_roles = data.get("pending_roles") or []
    behavior_nudge = data.get("behavior_nudge") or ""
    if retry_feedback:
        behavior_nudge = f"{retry_feedback} {behavior_nudge}".strip()
    # Phase C: one short season line, rendered ONLY when the engine sends a
    # season (GOODS_ENABLED) so flag-off prompts stay byte-identical.
    season = data.get("season")
    season_line = ""
    if season:
        winter_hint = " — stocks do not regrow; rely on stored food" if season == "winter" else ""
        season_line = f"Season: {season}{winter_hint}\n"
    # Phase D: the era (one line) replaces the vanity level when the engine
    # sends one (TECH_TREE_ENABLED); with the flag off the engine sends None
    # and this renders the exact Phase C level line.
    era = data.get("era")
    if era:
        tech_tier = data.get("village_tech_tier")
        tier_part = f" (tech tier {tech_tier})" if tech_tier else ""
        level_line = f"Era: {era}{tier_part}\n"
    else:
        level_line = f"Civilization level: {data.get('civilization_level', 1)}\n"
    # Phase E: one short prices line, rendered ONLY when the engine sends one
    # (ECONOMY_ENABLED and a market exists) so flag-off / no-market prompts
    # stay byte-identical to Phase D.
    prices_raw = data.get("prices_line")
    prices_line = f"Prices: {prices_raw}\n" if prices_raw else ""
    # Living-ecosystem Phase 5: one short line, rendered ONLY while the
    # engine's weather state is "storm"/"clearing" (WEATHER_GOVERNANCE_ENABLED
    # and WEATHER_ENABLED both on) -- same fold-in-only-when-set pattern as
    # prices_line/chronicle_line, so flag-off / clear-weather prompts stay
    # byte-identical to Phase 4 alone.
    weather_raw = data.get("weather_line")
    weather_line = f"Weather: {weather_raw}\n" if weather_raw else ""
    # Huntable wildlife hint: rendered ONLY when the engine reports prey in
    # HUNT_RADIUS (WILDLIFE_ENABLED), same fold-in-only-when-set pattern.
    wildlife_raw = data.get("nearby_wildlife_line")
    nearby_wildlife_line = f"{wildlife_raw}\n" if wildlife_raw else ""
    # Phase F: one-word life stage folded into the existing personality line
    # (no new template line -- near-zero token cost, and with the flag off
    # the engine sends life_stage=None so this renders byte-identical to
    # Phase E).
    life_stage = data.get("life_stage")
    personality_text = data.get("personality") or ""
    if life_stage:
        personality_text = f"{life_stage}, {personality_text}" if personality_text else life_stage
    # Phase G: practiced skill levels folded into the existing "Your skill:"
    # line (no new template line) -- only nonzero levels are shown so an
    # unpracticed agent's line stays exactly the Phase F role_skill text.
    role_skill_text = data.get("role_skill", "")
    skills = data.get("skills")
    if skills:
        practiced = ", ".join(f"{k} {v}" for k, v in skills.items() if v > 0)
        if practiced:
            role_skill_text = f"{role_skill_text} (practiced: {practiced})"
    # Phase G: one short rotating "Village history: ..." line, rendered ONLY
    # when the engine sends one (CULTURE_ENABLED and the chronicle has an
    # entry) so flag-off / empty-chronicle prompts stay byte-identical.
    chronicle_line_raw = data.get("chronicle_line")
    chronicle_line = f"Village history: {chronicle_line_raw}\n" if chronicle_line_raw else ""
    # Sovereign God mode (Phase 3 — Voice binding): public providence and
    # private omens use binding prompt lines requiring divine_response; Matrix
    # anoint/bush/story lines keep soft "interpret or ignore" wording.
    divine_lines_parts = []
    divine_public_raw = data.get("divine_public_line")
    if divine_public_raw:
        divine_lines_parts.append(_format_voice_guidance_line("public", divine_public_raw))
    divine_private_raw = data.get("divine_private_line")
    if divine_private_raw:
        divine_lines_parts.append(_format_voice_guidance_line("private", divine_private_raw))
    divine_bush_raw = data.get("divine_burning_bush_line")
    if divine_bush_raw:
        divine_lines_parts.append(
            f"Divine audience: {divine_bush_raw} You may respond in talk or reason.\n")
    divine_anoint_raw = data.get("divine_anointment_line")
    if divine_anoint_raw:
        divine_lines_parts.append(
            f"Anointed destiny: {divine_anoint_raw} You may interpret or ignore it.\n")
    divine_event_raw = data.get("divine_public_event_line")
    if divine_event_raw:
        divine_lines_parts.append(f"Divine story: {divine_event_raw} You may interpret or ignore it.\n")
    truth_raw = data.get("divine_simulation_truth_line")
    if truth_raw:
        divine_lines_parts.append(f"{truth_raw}\n")
    divine_lines = "".join(divine_lines_parts)
    council_digest_raw = data.get("council_digest_line")
    council_digest_line = (
        f"Recent council: {council_digest_raw}\n" if council_digest_raw else ""
    )
    lessons_raw = data.get("library_lessons")
    library_lessons_line = f"Library lessons: {lessons_raw}\n" if lessons_raw else ""
    path1_parts = []
    if data.get("path1_tool_line"):
        path1_parts.append(data["path1_tool_line"])
    if data.get("path1_industry_line"):
        path1_parts.append(data["path1_industry_line"])
    if data.get("path1_neighbor_line"):
        path1_parts.append(data["path1_neighbor_line"])
    if data.get("settlement_stores_line"):
        path1_parts.append(f"Settlement stores: {data['settlement_stores_line']}")
    path1_lines = ("\n".join(path1_parts) + "\n") if path1_parts else ""

    return USER_PROMPT_TEMPLATE.format(
        agent_name=data.get("agent_name"),
        role=data.get("role"),
        role_skill=role_skill_text,
        known_roles=", ".join(data.get("known_role_ids") or []) or "none",
        personality=personality_text,
        memory="none" if slim else compose_memory(data),
        hunger=data.get("hunger", 100),
        health=data.get("health", 100),
        resources=data.get("resources"),
        relationships=data.get("relationships"),
        beliefs=data.get("beliefs") or "none",
        belief_registry="; ".join(
            f"{b.get('id')} / {b.get('name')}: {b.get('tenet')}"
            for b in (data.get("belief_registry") or []) if isinstance(b, dict)
        ) or "none",
        belief_examples="; ".join(
            f"{b.get('id')} ({b.get('kind')}): {b.get('tenet')}"
            for b in (data.get("belief_examples") or []) if isinstance(b, dict)
        ) or "none",
        nearby_beliefs=data.get("nearby_beliefs") or "none",
        nearby_agents=nearby_formatted,
        nearby_wildlife_line=nearby_wildlife_line,
        world_zone=data.get("world_zone"),
        current_district=data.get("current_district", "none"),
        known_districts=format_known_districts(data.get("known_districts") or []),
        district_stocks=data.get("district_stocks") or "none",
        known_terraform=", ".join(data.get("known_terraform") or []) or "none",
        level_line=level_line,
        structures_built=data.get("structures_built", 0),
        active_project=data.get("active_project", "none"),
        project_progress=data.get("project_progress", "none"),
        directive=data.get("directive", "none"),
        divine_lines=divine_lines,
        invention_status=data.get("invention_status", "not needed"),
        commitment_text=format_commitment(data.get("commitment")),
        idle_agents=format_idle_agents(idle_agents),
        known_resources=format_known_resources(known_resources),
        known_recipes=format_known_recipes(data.get("known_recipes") or []),
        pending_blueprints=format_pending_blueprints(pending_blueprints),
        pending_recipes=format_pending_recipes(data.get("pending_recipes") or []),
        pending_roles="; ".join(
            f"{role.get('slug')} ({role.get('name')}; specialty: {', '.join(role.get('specialty') or []) or 'none'})"
            for role in pending_roles if isinstance(role, dict)
        ) or "none",
        approved_custom_projects=format_approved_custom(approved_custom_projects),
        reserved_structure_ids=format_reserved_structure_ids(approved_custom_projects, pending_blueprints),
        rejected_blueprints=format_rejected_blueprints(rejected_blueprints),
        pending_rules=format_pending_rules(data.get("pending_rules") or []),
        active_rules=format_active_rules(data.get("active_rules") or []),
        constitution=format_constitution(data.get("constitution") or []),
        season_line=season_line,
        prices_line=prices_line,
        weather_line=weather_line,
        chronicle_line=chronicle_line,
        council_digest_line=council_digest_line,
        library_lessons_line=library_lessons_line,
        path1_lines=path1_lines,
        recent_conversations="none" if slim else data.get("recent_conversations", "none"),
        inbox=data.get("inbox", "none"),
        module_reports=data.get("module_reports", "none"),
        behavior_nudge=behavior_nudge,
        available_actions=data.get("available_actions"),
    )


_last_routine_system_prompt = None
_system_prompt_mismatch_warned = False


def _check_system_prompt_stability(system_content):
    """Log-once guard (Phase 2, TASKS_PENDING #2a): warn if the routine-turn
    system message differs from the one seen on a prior routine turn this
    session. Ollama (llama.cpp-based, same as LM Studio) reuses KV cache by longest common prefix per slot, so
    a system-prompt mutation mid-session would silently forfeit that reuse
    for every subsequent call -- this makes it observable instead.

    Fast path is an identity check (`is`): SYSTEM_PROMPT is a module global
    that should only ever be assigned once (module load) or, under
    TECH_TREE_ENABLED, rewritten once immediately after via .replace() before
    any request is served -- never reassigned once serving starts. Identity
    equality costs nothing per call. Only on an identity mismatch do we fall
    back to a value comparison (and then a hash for the warning message),
    so this never crashes and adds no per-call overhead in the expected
    (stable) case.

    Not called at all when SYSTEM_PROMPT_AT_LOAD_TIME is True and the turn is
    the omitted-system-message case (see build_decision_payload) -- omitting
    the message on purpose is not a "changed prefix", it is a different,
    intentional call shape, so routing it through this guard would fire a
    spurious mismatch warning against the last turn that DID send a system
    message (e.g. a slim retry or an invention turn in between)."""
    global _last_routine_system_prompt, _system_prompt_mismatch_warned
    if _last_routine_system_prompt is None:
        _last_routine_system_prompt = system_content
        return
    if system_content is _last_routine_system_prompt:
        return
    if system_content == _last_routine_system_prompt:
        _last_routine_system_prompt = system_content
        return
    if not _system_prompt_mismatch_warned:
        _system_prompt_mismatch_warned = True
        old_sha = hashlib.sha256(_last_routine_system_prompt.encode()).hexdigest()[:12]
        new_sha = hashlib.sha256(system_content.encode()).hexdigest()[:12]
        print(f"[server] WARNING: system prompt changed mid-session (cache "
              f"invalidated) old_sha256={old_sha} new_sha256={new_sha}")
    _last_routine_system_prompt = system_content


def build_decision_payload(data, self_prompt, response_format, slim=False, retry_feedback=None):
    """Assemble the internal chat-completion-shaped payload for a decision
    call (converted to Ollama's /api/chat wire body by to_ollama_body() at
    the actual POST site). slim=True builds the reduced-context retry payload (see
    run_agent_decision): SYSTEM_PROMPT_SLIM instead of SYSTEM_PROMPT (drops
    the worked EXAMPLE blocks) plus the slim user prompt. The rules and JSON
    schema are kept either way so response_format still shapes the output.
    invention_only turns always use INVENTION_SYSTEM_PROMPT instead (the
    ~20-rule village rulebook is irrelevant to authoring a blueprint and
    slim/full made almost no size difference for these calls -- see its
    docstring), regardless of the slim flag.

    retry_feedback (Phase 5, DECISION_RETRY_ENABLED): an optional
    concrete-reason string for the single answer-quality retry in
    run_agent_decision (unparseable JSON or a _fallback-stamped
    normalize_decision result). Threaded into build_user_prompt() for
    routine/high-stakes turns and into the sprite/invention prompt builders;
    build_council_user_prompt() (prompts.py) takes no such parameter, so for
    council turns the feedback is prefixed onto its returned user_content
    below instead -- same effect, no change to prompts.py's signature.

    SYSTEM_PROMPT_AT_LOAD_TIME (Phase 6, dark by default): when True, the
    primary (non-slim) routine/high-stakes dispatch -- i.e. everything that
    isn't sprite_design_only, invention_only, or the slim retry -- omits the
    system message entirely and routes to MODEL_SMART_SYS, relying on that
    model's baked Modelfile SYSTEM directive (generated from this exact
    SYSTEM_PROMPT text by `scripts/ollama_setup.py --with-system`) instead of
    sending it every call. Sprite/invention turns and the slim retry are
    unaffected -- they always send their own explicit system message and stay
    on MODEL_SMART (see the flag's definition for the full rationale)."""
    omit_system_prompt = False
    if data.get("sprite_design_only"):
        system_content = SPRITE_UPGRADE_SYSTEM_PROMPT
    elif data.get("invention_only"):
        system_content = INVENTION_SYSTEM_PROMPT
    elif data.get("council_turn"):
        system_content = COUNCIL_SYSTEM_PROMPT
    else:
        system_content = SYSTEM_PROMPT_SLIM if slim else SYSTEM_PROMPT
        if not slim:
            if SYSTEM_PROMPT_AT_LOAD_TIME:
                # The baked Modelfile SYSTEM directive applies instead (see
                # MODEL_SMART_SYS's definition) -- omitting the message here
                # is the intended, non-spurious case, so the stability guard
                # below (designed to catch an *unintended* mid-session prompt
                # mutation) must not run against it.
                omit_system_prompt = True
            else:
                # Only the primary (non-retry) routine dispatch is checked: the
                # slim retry deliberately swaps to SYSTEM_PROMPT_SLIM on overflow
                # and is expected to forfeit cache reuse for that one call (see
                # specs/03-cognition.md KV-cache prefix stability note).
                _check_system_prompt_stability(system_content)
    # Persona goes at the TOP OF THE USER MESSAGE, not appended to the system
    # prompt: Ollama (llama.cpp-based, same as LM Studio) reuses KV cache by
    # longest common prefix per slot, so
    # per-agent text inside the system message forced full prompt
    # reprocessing (~5k tokens) on every agent rotation. With the system
    # prompt byte-identical across agents it becomes a shared cached prefix.
    if data.get("council_turn"):
        user_content = _prompts.build_council_user_prompt(data)
        if retry_feedback:
            user_content = f"RETRY (previous reply rejected): {retry_feedback}\n\n" + user_content
    else:
        user_content = build_user_prompt(data, slim=slim, retry_feedback=retry_feedback)
    if self_prompt:
        user_content = (f"YOUR PERSONA (act in character): {self_prompt}\n\n"
                        + user_content)
    # Computed once and reused for both the max_tokens override below and the
    # sampling branch further down, so the two conditions can't drift apart.
    thinking_active = is_high_stakes_turn(data) and THINKING_ENABLED_HIGH_STAKES
    max_tokens, temperature = 512, 0.4
    if data.get("sprite_design_only"):
        max_tokens = 768
        temperature = 0.3
    elif data.get("invention_only"):
        # Phase D experiment hook: per-call overrides for invention-only turns.
        if INVENTION_MAX_TOKENS is not None:
            max_tokens = INVENTION_MAX_TOKENS
        if INVENTION_TEMPERATURE is not None:
            temperature = INVENTION_TEMPERATURE
    elif thinking_active:
        # Phase 2: high-stakes turns with thinking re-enabled need extra
        # budget for reasoning output on top of the decision JSON.
        max_tokens = HIGH_STAKES_MAX_TOKENS
    messages = [] if omit_system_prompt else [{"role": "system", "content": system_content}]
    messages.append({"role": "user", "content": user_content})
    payload = {
        "model": MODEL_SMART_SYS if omit_system_prompt else model_for_decision(data),
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if thinking_active:
        payload.update(THINKING_SAMPLING)
        # thinking_active turns intentionally omit "think" -- see
        # to_ollama_body's docstring: omitted means the model may think.
    else:
        payload.update(NON_THINKING_SAMPLING)
        if DISABLE_THINKING_ROUTINE:
            payload["think"] = False
        if ROUTINE_PRESENCE_PENALTY:
            payload["presence_penalty"] = ROUTINE_PRESENCE_PENALTY
    if response_format is not None:
        payload["response_format"] = response_format
    divine_sampling = data.get("divine_sampling")
    if isinstance(divine_sampling, dict):
        model_key = divine_sampling.get("model")
        if model_key == "sim-fast":
            payload["model"] = MODEL_FAST
        elif model_key == "sim-smart":
            payload["model"] = MODEL_SMART_SYS if omit_system_prompt else MODEL_SMART
        if "temperature" in divine_sampling:
            payload["temperature"] = divine_sampling["temperature"]
        for key in ("top_p", "top_k", "min_p"):
            if key in divine_sampling:
                payload[key] = divine_sampling[key]
    return payload


def is_context_overflow_error(err_text, err_type=None):
    """True for Ollama's context-overflow signal (docs/ollama_config.md
    'Overflow / truncation contract', Phase 0 finding #5): HTTP 400 with
    error.type == "exceed_context_size_error" is the structured, preferred
    signal; a message mentioning both "context" and "exceed" is the fallback
    for any future Ollama build that changes the type string. This replaces
    LM Studio's "context size has been exceeded" string-sniff -- Ollama does
    NOT silently truncate (confirmed live), it errors instead."""
    if err_type == "exceed_context_size_error":
        return True
    low = (err_text or "").lower()
    return "context" in low and "exceed" in low


def score_belief_pitch_decision(decision, data):
    """Attach a model score to a validated belief pitch when the engine's
    bounded budget says one remains. This runs in the decision worker, after
    the normal request and never while SimEngine's state lock is held."""
    if not isinstance(decision, dict) or decision.get("action") != "talk_to_nearby":
        return decision
    pitch = decision.get("belief_pitch")
    if not isinstance(pitch, dict) or not data.get("belief_pitch_budget_remaining"):
        return decision
    belief_id = pitch.get("belief_id")
    belief = next((b for b in data.get("belief_registry") or []
                   if isinstance(b, dict) and b.get("id") == belief_id), None)
    if not belief or belief_id not in (data.get("belief_ids") or []):
        return decision
    listener = decision.get("target")
    nearby_beliefs = data.get("nearby_beliefs") or {}
    # The engine's payload includes only agents within the existing 80px talk
    # radius. Ordinary talk may target a distant agent and walk toward them,
    # but do not spend a pitch-scoring call until this is a real conversation.
    if listener not in nearby_beliefs:
        return decision
    # The per-world cap is authoritatively checked/incremented under the
    # engine lock when the score is applied. Concurrent decision workers can
    # observe the same remaining budget, so at most MAX_CONCURRENT_LLM stale
    # score calls can overshoot external LM spend; late scores are ignored by
    # the engine rather than mutating belief state past the cap.
    quality = run_belief_pitch(
        data.get("agent_name"), listener, belief, pitch.get("pitch"),
        (data.get("relationships") or {}).get(listener, "neutral"),
        nearby_beliefs.get(listener) or [], data.get("frame_tick"),
    )
    if quality is not None:
        decision["belief_pitch_quality"] = quality
        decision["belief_pitch_scored"] = True
    return decision


# Every *_rejection_note key normalize_decision can stamp onto a fallback
# decision (see its rejection branches) -- checked in this order by
# _rejection_feedback_text (Phase 5) to build the decision retry's concrete
# feedback line. Kept as one tuple so a future rejection-note key only needs
# adding here, not at every call site.
_REJECTION_NOTE_KEYS = (
    "sprite_rejection_note",
    "council_rejection_note",
    "terraform_rejection_note",
    "upgrade_rejection_note",
    "rejection_note",
)


def _rejection_feedback_text(decision):
    """Pull the concrete reason off a normalize_decision fallback (Phase 5
    decision retry), preferring whichever *_rejection_note key is present --
    see _REJECTION_NOTE_KEYS. Returns None if the fallback carries no note
    (e.g. an invalid talk_to_nearby, which is redirected without a note)."""
    if not isinstance(decision, dict):
        return None
    for key in _REJECTION_NOTE_KEYS:
        note = decision.get(key)
        if note:
            return str(note)
    return None


def _truncation_retry_feedback(sprite_turn):
    """Shared wording for the Phase 5 decision retry whenever the prior
    response's done_reason was "length" (Ollama's max_tokens cut generation
    off mid-object -- the Phase 0 probe's dominant real-world failure mode).
    Used at BOTH retry trigger points in run_agent_decision, factored here so
    they cannot drift into two different strings: extract_json_decision's
    truncated-JSON salvager (deliberately NOT changed by this fix -- it is
    load-bearing elsewhere) can turn a cut-off reply into a
    syntactically-parseable-but-incomplete decision that skips the
    unparseable-JSON trigger point and lands on the _fallback trigger point
    instead (e.g. a sprite turn missing its grid, rejected by
    normalize_decision), so both points need this exact message rather than
    the bare *_rejection_note, which would otherwise read as a
    malformed-answer complaint and prompt the model to re-send another
    oversized reply.

    A DECISION_SCHEMA grammar bound on sprite.grid (Phase 4) already makes
    runaway sprite grids far rarer at generation time; this retry wording is
    the backstop for when that bound doesn't apply (e.g. structured output
    disabled session-wide after a format-degrade -- see
    _structured_output_enabled)."""
    grid_hint = " (for example, a smaller sprite grid)" if sprite_turn else ""
    return (
        "your previous reply was cut off before it finished (it ran out of "
        f"output space); reply with a smaller, more compact JSON object{grid_hint}."
    )


def run_agent_decision(data):
    """Build the prompt, call Ollama, and return a validated decision dict.

    Shared by the HTTP /agent/think endpoint and the server-authoritative
    SimEngine's think worker. Returns a plain dict (already normalized) — on any
    failure it returns either an {"error": ...} dict (engine maps these to its
    offline/compute/rest paths) or a role fallback decision."""
    try:
        # Resolve is_high_stakes_turn (and consume the extra-thinking budget,
        # if applicable) exactly once for this request. build_decision_payload
        # (model_for_decision, THINKING_SAMPLING) and the timeout choice below
        # both call is_high_stakes_turn(), which will echo this stamped value
        # instead of re-evaluating -- see resolve_high_stakes()'s docstring.
        high_stakes_active, high_stakes_capped = resolve_high_stakes(data)
        self_prompt = (data.get("self_prompt") or "").strip()
        response_format = build_response_format()
        payload = build_decision_payload(data, self_prompt, response_format)
        request_timeout = THINKING_TIMEOUT_S if is_high_stakes_turn(data) else DEFAULT_TIMEOUT_S

        known_resources = data.get("known_resources") or []
        pending_blueprints = data.get("pending_blueprints") or []
        approved_custom_projects = data.get("approved_custom_projects") or []
        rejected_blueprints = data.get("rejected_blueprints") or []
        nearby_formatted = format_nearby_agents(data.get("nearby_agents"))

        agent_name = data.get("agent_name")
        frame_tick = data.get("frame_tick")
        agent_data = build_agent_data(
            data, nearby_formatted, known_resources, pending_blueprints,
            approved_custom_projects, rejected_blueprints,
        )

        def log_lm(latency_ms, response=None, http_status=None, decision=None, error=None,
                   fallback_extra=None):
            # Measure the payload actually sent -- `payload` is reassigned in
            # place if the context-overflow retry (or, Phase 5, the
            # answer-quality decision retry) swaps in a different payload, so
            # reading it here (not capturing sizes earlier) reflects that.
            # decision_retries is read the same way (by name, at call time)
            # so it's always the value as-of whenever log_lm actually runs --
            # 0 on the common path, 1 once the Phase 5 retry has fired.
            messages = payload.get("messages") or []
            system_chars = sum(len(m.get("content") or "") for m in messages if m.get("role") == "system")
            prompt_chars = sum(len(m.get("content") or "") for m in messages if m.get("role") == "user")
            record = {
                "agent_name": agent_name,
                "frame_tick": frame_tick,
                "latency_ms": latency_ms,
                "invention_only": bool(data.get("invention_only")),
                "sprite_design_only": bool(data.get("sprite_design_only")),
                "high_stakes_reason": data.get("high_stakes_reason"),
                "high_stakes_active": high_stakes_active,
                "high_stakes_capped": high_stakes_capped,
                "prompt_chars": prompt_chars,
                "system_chars": system_chars,
                "nudges_total": data.get("nudges_total"),
                "nudges_dropped": data.get("nudges_dropped"),
                "decision_retries": decision_retries,
                "request": payload,
                "response": response,
                "http_status": http_status,
                "decision": decision,
                "error": error,
            }
            # Phase 6 (Fix 5): only bad_response_fallback ever passes
            # fallback_extra, and only with FALLBACK_AI_CHOICE_ENABLED on --
            # every ordinary turn's log_lm call leaves this None, so these
            # keys are simply absent from the record rather than present
            # with null/false values.
            if fallback_extra:
                record.update(fallback_extra)
            session_logger.log_lm_exchange(record)

        def bad_response_fallback(latency_ms, response=None, http_status=None, error="bad_response"):
            fallback, fallback_extra = _terminal_fallback(agent_data.get("role"), agent_data)
            log_lm(latency_ms, response=response, http_status=http_status,
                   decision=fallback, error=error, fallback_extra=fallback_extra)
            return fallback

        global _structured_output_enabled, _model_routing_enabled

        # Ollama does not cancel server-side generation when a client
        # aborts/times out a stream:false request (Phase 0 operational
        # finding, ollama_config.md) -- an orphaned timed-out request keeps
        # consuming a queue slot. Do not add a retry-on-timeout loop here;
        # every requests.exceptions.RequestException path below (including
        # Timeout) returns immediately instead of firing a second request.
        def _request_error_tag(exc):
            if isinstance(exc, requests.exceptions.Timeout):
                return "llm timeout"
            return "llm offline"

        # Every requests.post(OLLAMA_CHAT_URL, ...) in this turn (initial call,
        # format-degrade retry, context-overflow slim retry, and any future
        # call site) routes through here so a single run_agent_decision
        # invocation can never fire more than LLM_CALLS_PER_TURN_MAX requests
        # -- llm_calls_made is local to this invocation, not shared/global.
        # Raises LLMBudgetExhausted (never a RequestException) once the
        # budget is spent so callers can refuse distinguishably from a real
        # network failure and take their existing fallback path.
        llm_calls_made = 0

        def _post_ollama(body, timeout):
            nonlocal llm_calls_made
            if llm_calls_made >= LLM_CALLS_PER_TURN_MAX:
                raise LLMBudgetExhausted()
            llm_calls_made += 1
            return requests.post(OLLAMA_CHAT_URL, json=body, timeout=timeout)

        # Phase 6 (Fix 5, FALLBACK_AI_CHOICE_ENABLED): called only from
        # bad_response_fallback, i.e. only once Fix 4's retry is exhausted
        # with no usable decision at all -- unparseable JSON, a missing
        # `message` key, or a non-recoverable error body. Every network-level
        # failure (llm offline/timeout, compute_error, model_not_found, llm
        # budget exhausted) returns directly from its own call site above
        # without ever reaching bad_response_fallback, so this can never fire
        # for those. Returns (decision, extra_log_fields); extra_log_fields
        # is {} when the flag is off, so log_lm emits nothing extra and
        # behavior is byte-identical to pre-Phase-6.
        def _terminal_fallback(role, agent_data):
            if not FALLBACK_AI_CHOICE_ENABLED:
                return role_fallback_action(role, agent_data), {}

            candidates = role_fallback_candidates(role, agent_data, limit=3)
            if not candidates:
                # Unreachable in practice (the ladder's final branch always
                # yields a candidate) -- fall back to the plain ladder call
                # rather than ever returning no decision.
                decision = role_fallback_action(role, agent_data)
                return decision, {
                    "fallback_triggered": True,
                    "fallback_candidate_count": 0,
                    "fallback_selection_method": "priority_default",
                    "fallback_candidates": [],
                }

            extra = {
                "fallback_triggered": True,
                "fallback_candidate_count": len(candidates),
                "fallback_candidates": [
                    {"action": c.get("action"), "target": c.get("target")} for c in candidates
                ],
            }

            if len(candidates) < 2 or llm_calls_made >= LLM_CALLS_PER_TURN_MAX:
                # Only one safe option, or no budget left to ask -- the
                # highest-priority candidate is the answer either way; no
                # call is made, so no fallback_ai_latency_ms is logged.
                chosen = candidates[0]
                extra["fallback_selection_method"] = (
                    "single_candidate" if len(candidates) < 2 else "priority_default"
                )
                chosen["_fallback"] = True
                return chosen, extra

            # >=2 candidates and budget remains: ask the fast model to pick
            # one letter. Minimal prompt, minimal tokens -- this is a
            # tiebreak, not a decision turn -- and never retried; any
            # failure/timeout/unparseable reply below falls back to the
            # first (highest-priority) candidate.
            labels = ["A", "B", "C", "D", "E"][:len(candidates)]
            option_lines = []
            for label, cand in zip(labels, candidates):
                target = cand.get("target")
                option_lines.append(
                    f"{label}: {cand.get('action')}" + (f" -> {target}" if target else "")
                )
            choice_prompt = (
                "Pick the best next action for your villager from these safe options:\n"
                + "\n".join(option_lines)
                + f"\nReply with a single letter ({', '.join(labels)}) and nothing else."
            )
            choice_payload = {
                "model": MODEL_FAST,
                "messages": [
                    {"role": "system",
                     "content": "You choose one lettered option. Reply with only the letter."},
                    {"role": "user", "content": choice_prompt},
                ],
                "max_tokens": 5,
                "temperature": 0.2,
                **NON_THINKING_SAMPLING,
            }
            if DISABLE_THINKING_ROUTINE:
                choice_payload["think"] = False

            choice_start = datetime.now()
            try:
                choice_resp = _post_ollama(to_ollama_body(choice_payload), FALLBACK_AI_CHOICE_TIMEOUT_S)
            except (LLMBudgetExhausted, requests.exceptions.RequestException):
                extra["fallback_ai_latency_ms"] = int(
                    (datetime.now() - choice_start).total_seconds() * 1000)
                extra["fallback_selection_method"] = "priority_default"
                chosen = candidates[0]
                chosen["_fallback"] = True
                return chosen, extra
            extra["fallback_ai_latency_ms"] = int((datetime.now() - choice_start).total_seconds() * 1000)

            picked = None
            try:
                choice_body = choice_resp.json()
            except ValueError:
                choice_body = None
            choice_text = lm_message_text(choice_body.get("message")) if isinstance(choice_body, dict) else ""
            first_char = (choice_text or "").strip()[:1].upper()
            if first_char in labels:
                picked = candidates[labels.index(first_char)]

            if picked is None:
                extra["fallback_selection_method"] = "priority_default"
                chosen = candidates[0]
            else:
                extra["fallback_selection_method"] = "ai_choice"
                chosen = picked
            chosen["_fallback"] = True
            return chosen, extra

        # Phase 5 (DECISION_RETRY_ENABLED): 0 on the common path, set to 1 the
        # moment (and only once) an answer-quality retry fires -- guards both
        # retry trigger points below so a turn can retry for unparseable JSON
        # OR for a _fallback-stamped decision, never both. log_lm reads this
        # by name (see its docstring), so it doesn't need threading through
        # every log_lm() call site.
        decision_retries = 0

        def _decision_retry(feedback_text, slim_used):
            """POST the single answer-quality retry through _post_ollama (so
            it spends from the same LLM_CALLS_PER_TURN_MAX budget as every
            other call site) with feedback_text folded into the retry's user
            prompt via build_decision_payload's retry_feedback kwarg. Returns
            (lm_body, http_status, decision) where decision is None if the
            retry itself came back unparseable/erroring -- callers then take
            the existing fallback path per spec (no second retry). Deliberately
            does NOT catch LLMBudgetExhausted / requests.exceptions.RequestException
            -- those propagate to the caller, which handles them exactly like
            every other call site in this function (immediate {"error": ...}
            return, no fallback, no further retry)."""
            nonlocal payload
            retry_payload = build_decision_payload(
                data, self_prompt, response_format, slim=slim_used, retry_feedback=feedback_text)
            payload = retry_payload
            resp = _post_ollama(to_ollama_body(retry_payload), request_timeout)
            http_status2 = resp.status_code
            try:
                lm_body2 = resp.json()
            except ValueError:
                lm_body2 = None
            decision2 = None
            if not (lm_body2 is None or (isinstance(lm_body2, dict) and lm_body2.get("error"))):
                try:
                    message2 = lm_body2["message"]
                except (TypeError, KeyError):
                    message2 = None
                if message2 is not None:
                    if isinstance(message2, dict):
                        message2.setdefault("reasoning_content", "")
                    raw_text2 = lm_message_text(message2)
                    decision2 = extract_json_decision(raw_text2)
                    if not decision2 and isinstance(message2, dict):
                        decision2 = extract_json_decision(message2.get("content") or "")
            return lm_body2, http_status2, decision2

        start = datetime.now()
        try:
            resp = _post_ollama(to_ollama_body(payload), request_timeout)
        except LLMBudgetExhausted:
            latency_ms = int((datetime.now() - start).total_seconds() * 1000)
            log_lm(latency_ms, error="llm budget exhausted")
            return {"error": "llm budget exhausted", "action": "rest"}
        except requests.exceptions.RequestException as exc:
            latency_ms = int((datetime.now() - start).total_seconds() * 1000)
            err = _request_error_tag(exc)
            log_lm(latency_ms, error=err)
            return {"error": err, "action": "rest"}

        latency_ms = int((datetime.now() - start).total_seconds() * 1000)
        http_status = resp.status_code

        try:
            lm_body = resp.json()
        except ValueError:
            lm_body = None

        # Auto-degrade: if Ollama rejected the `format` (JSON-schema) field,
        # disable structured output for the session and retry once so this
        # turn still succeeds. Ollama's format param is stable (Phase 0
        # finding #2) so this is expected to fire rarely, but the safety net
        # stays in place.
        if ("response_format" in payload and _structured_output_enabled
                and looks_like_response_format_error(http_status, lm_body)):
            print("[server] Ollama rejected the format/json-schema field; disabling "
                  "structured output for this session and retrying without it.")
            _structured_output_enabled = False
            payload.pop("response_format", None)
            response_format = None
            start = datetime.now()
            try:
                resp = _post_ollama(to_ollama_body(payload), request_timeout)
            except LLMBudgetExhausted:
                latency_ms = int((datetime.now() - start).total_seconds() * 1000)
                log_lm(latency_ms, error="llm budget exhausted")
                return {"error": "llm budget exhausted", "action": "rest"}
            except requests.exceptions.RequestException as exc:
                latency_ms = int((datetime.now() - start).total_seconds() * 1000)
                err = _request_error_tag(exc)
                log_lm(latency_ms, error=err)
                return {"error": err, "action": "rest"}
            latency_ms = int((datetime.now() - start).total_seconds() * 1000)
            http_status = resp.status_code
            try:
                lm_body = resp.json()
            except ValueError:
                lm_body = None

        # A missing routed model id is a setup failure, not a transient
        # condition -- Ollama has no LM-Studio-style "local-model" alias to
        # fall back to. Log once per session and return the offline fallback
        # rather than retrying (see scripts/ollama_setup.py).
        if looks_like_model_not_found_error(http_status, lm_body):
            if _model_routing_enabled:
                print(f"[server] Ollama doesn't have model {payload.get('model')!r} "
                      f"created/loaded -- this is a setup failure. Run "
                      f"`uv run python scripts/ollama_setup.py` then restart the server.")
                _model_routing_enabled = False
            log_lm(latency_ms, response=lm_body, http_status=http_status, error="model_not_found")
            return {"error": "llm offline", "action": "rest"}

        if lm_body is None:
            return bad_response_fallback(latency_ms, http_status=http_status)

        # error_kind tags the whole call for logging once at the end (below),
        # even if the context-overflow retry ultimately recovers a decision --
        # this is what makes context_overflow measurable/distinguishable in
        # llm.jsonl per the plan, without double-logging each attempt.
        error_kind = None
        if isinstance(lm_body, dict) and lm_body.get("error"):
            err_text, err_type = _ollama_error_parts(lm_body)
            if "compute error" in err_text.lower():
                log_lm(latency_ms, response=lm_body, http_status=http_status, error="compute_error")
                return {"error": "compute_error", "action": "rest"}
            if not is_context_overflow_error(err_text, err_type):
                return bad_response_fallback(latency_ms, response=lm_body, http_status=http_status)

            # Retry ONCE with a slimmed-down payload: no memory line, no
            # recent conversations, no worked examples. Rules + JSON schema
            # are kept so response_format still shapes the output. On any
            # further failure this falls through to the normal handling below
            # (which will hit bad_response_fallback), so there is no loop.
            error_kind = "context_overflow"
            slim_payload = build_decision_payload(data, self_prompt, response_format, slim=True)
            payload = slim_payload
            retry_start = datetime.now()
            try:
                resp = _post_ollama(to_ollama_body(slim_payload), request_timeout)
            except LLMBudgetExhausted:
                latency_ms += int((datetime.now() - retry_start).total_seconds() * 1000)
                log_lm(latency_ms, error="llm budget exhausted")
                return {"error": "llm budget exhausted", "action": "rest"}
            except requests.exceptions.RequestException as exc:
                latency_ms += int((datetime.now() - retry_start).total_seconds() * 1000)
                err = _request_error_tag(exc)
                log_lm(latency_ms, error=err)
                return {"error": err, "action": "rest"}
            latency_ms += int((datetime.now() - retry_start).total_seconds() * 1000)
            http_status = resp.status_code
            try:
                lm_body = resp.json()
            except ValueError:
                lm_body = None

            if lm_body is None:
                return bad_response_fallback(latency_ms, http_status=http_status, error=error_kind)
            if isinstance(lm_body, dict) and lm_body.get("error"):
                return bad_response_fallback(latency_ms, response=lm_body, http_status=http_status, error=error_kind)

        try:
            message = lm_body["message"]
        except (TypeError, KeyError):
            return bad_response_fallback(latency_ms, response=lm_body, http_status=http_status,
                                          error=error_kind or "bad_response")
        if isinstance(message, dict):
            # No reasoning_content channel exists in Ollama's response shape
            # (that was purely an LM Studio quirk) -- keep the key present as
            # an empty string in the logged response for log-shape
            # compatibility with any downstream tooling that still reads it
            # (e.g. scripts/llm_replay_bench.py's thinking-leak analysis,
            # ported to the new shape in Phase 4).
            message.setdefault("reasoning_content", "")

        raw_text = lm_message_text(message)
        decision = extract_json_decision(raw_text)
        if not decision and isinstance(message, dict):
            decision = extract_json_decision(message.get("content") or "")
        if not decision:
            # Phase 5, failure point 1/2: unparseable JSON. Retry once
            # (budget permitting) with a concrete-reason feedback line before
            # falling back -- see DECISION_RETRY_ENABLED's docstring for why
            # this never covers network-level failures.
            if DECISION_RETRY_ENABLED and decision_retries == 0:
                decision_retries = 1
                # Phase 0 probe finding (see the plan): the dominant real
                # cause of unparseable replies is generation truncation --
                # Ollama's done_reason == "length" means max_tokens cut the
                # JSON off mid-object, not a malformed answer. A generic
                # "could not be parsed" message would mislead the model into
                # re-emitting the same oversized answer, so call that out by
                # name and ask for something smaller instead.
                done_reason = lm_body.get("done_reason") if isinstance(lm_body, dict) else None
                if done_reason == "length":
                    feedback_text = _truncation_retry_feedback(data.get("sprite_design_only"))
                else:
                    feedback_text = (
                        "your previous reply could not be parsed as JSON; reply with only "
                        "the JSON decision object."
                    )
                retry_start = datetime.now()
                try:
                    lm_body, http_status, decision = _decision_retry(
                        feedback_text, slim_used=(error_kind == "context_overflow"))
                except LLMBudgetExhausted:
                    latency_ms += int((datetime.now() - retry_start).total_seconds() * 1000)
                    log_lm(latency_ms, error="llm budget exhausted")
                    return {"error": "llm budget exhausted", "action": "rest"}
                except requests.exceptions.RequestException as exc:
                    latency_ms += int((datetime.now() - retry_start).total_seconds() * 1000)
                    err = _request_error_tag(exc)
                    log_lm(latency_ms, error=err)
                    return {"error": err, "action": "rest"}
                latency_ms += int((datetime.now() - retry_start).total_seconds() * 1000)
                if not decision:
                    # Second attempt also failed -- existing fallback path,
                    # no further retry.
                    return bad_response_fallback(latency_ms, response=lm_body, http_status=http_status,
                                                  error=error_kind or "bad_response")
            else:
                return bad_response_fallback(latency_ms, response=lm_body, http_status=http_status,
                                              error=error_kind or "bad_response")

        # Held in its own local (rather than nested inline) so it can be
        # inspected for the _fallback stamp (role_fallback_action) before the
        # belief-pitch scoring / divine-response synthesis passes run over it,
        # and so failure point 2/2 (Phase 5) below can trigger the same
        # single answer-quality retry when normalize_decision rejects it.
        normalized_decision = normalize_decision(decision, agent_data)
        if (DECISION_RETRY_ENABLED and decision_retries == 0
                and isinstance(normalized_decision, dict) and normalized_decision.get("_fallback")):
            decision_retries = 1
            # Bug fix (found in Phase 5 verification): extract_json_decision's
            # truncated-JSON salvager (see _truncation_retry_feedback's
            # docstring) can turn a done_reason == "length" reply into a
            # decision that parses fine but is missing a required field --
            # e.g. a sprite turn whose grid got cut off -- which skips the
            # unparseable-JSON trigger point above and lands here instead,
            # rejected by normalize_decision. lm_body has NOT been reassigned
            # yet at this point (this whole block only runs when
            # decision_retries was still 0, i.e. the unparseable-JSON retry
            # never fired), so it is still the ORIGINAL response -- reading
            # its done_reason here is safe. When that response was truncated,
            # lead with the same truncation wording as the other trigger
            # point instead of the bare *_rejection_note (which reads as a
            # malformed-answer complaint and would prompt the model to
            # re-send another oversized reply); keep the note as supporting
            # detail since it still names which field was lost.
            done_reason = lm_body.get("done_reason") if isinstance(lm_body, dict) else None
            rejection_note = _rejection_feedback_text(normalized_decision)
            if done_reason == "length":
                feedback_text = _truncation_retry_feedback(data.get("sprite_design_only"))
                if rejection_note:
                    feedback_text = f"{feedback_text} ({rejection_note})"
            else:
                feedback_text = rejection_note or (
                    "your previous reply could not be parsed as JSON; reply with only "
                    "the JSON decision object."
                )
            retry_start = datetime.now()
            try:
                lm_body, http_status, retry_decision = _decision_retry(
                    feedback_text, slim_used=(error_kind == "context_overflow"))
            except LLMBudgetExhausted:
                latency_ms += int((datetime.now() - retry_start).total_seconds() * 1000)
                log_lm(latency_ms, error="llm budget exhausted")
                return {"error": "llm budget exhausted", "action": "rest"}
            except requests.exceptions.RequestException as exc:
                latency_ms += int((datetime.now() - retry_start).total_seconds() * 1000)
                err = _request_error_tag(exc)
                log_lm(latency_ms, error=err)
                return {"error": err, "action": "rest"}
            latency_ms += int((datetime.now() - retry_start).total_seconds() * 1000)
            if retry_decision:
                normalized_decision = normalize_decision(retry_decision, agent_data)
            # else: retry itself came back unparseable/erroring -- the
            # existing fallback path IS normalized_decision as already
            # computed above (already a role_fallback_action dict), so there
            # is nothing further to do; fall through and log/return it.

        # Phase 6 (Fix 5): this is the motivating case for the whole plan --
        # normalize_decision rejected a syntactically valid decision and
        # Fix 4's retry is exhausted, whether it fired just above and still
        # landed on a fallback, or never fired at all (DECISION_RETRY_ENABLED
        # off, or decision_retries already spent by the unparseable-JSON
        # failure point earlier in this function). Never reached for a
        # network failure -- this whole function body only gets here once
        # Ollama has actually responded with something normalize_decision
        # could evaluate. With FALLBACK_AI_CHOICE_ENABLED off this block is
        # skipped entirely, so normalized_decision (and its baked
        # *_rejection_note/reasoning) is returned exactly as
        # normalize_decision produced it -- byte-identical to pre-Phase-6
        # behavior, no fallback_* log fields, no extra call.
        fallback_extra = None
        if (FALLBACK_AI_CHOICE_ENABLED and isinstance(normalized_decision, dict)
                and normalized_decision.get("_fallback")):
            # Carry the specific *_rejection_note/reasoning normalize_decision
            # already baked onto the rejected fallback (this is what makes
            # llm.jsonl greppable and feeds the cross-turn behavior_nudge --
            # see _rejection_feedback_text/_REJECTION_NOTE_KEYS) onto
            # whatever _terminal_fallback returns, so swapping in the
            # AI-choice tiebreak doesn't silently drop that diagnostic.
            carried_notes = {
                key: normalized_decision[key]
                for key in _REJECTION_NOTE_KEYS if normalized_decision.get(key)
            }
            carried_reasoning = normalized_decision.get("reasoning")
            normalized_decision, fallback_extra = _terminal_fallback(agent_data.get("role"), agent_data)
            if carried_notes:
                normalized_decision.update(carried_notes)
            if carried_reasoning:
                normalized_decision["reasoning"] = carried_reasoning

        decision = synthesize_divine_response(
            score_belief_pitch_decision(normalized_decision, data),
            agent_data,
        )

        log_lm(latency_ms, response=lm_body, http_status=http_status, decision=decision, error=error_kind,
               fallback_extra=fallback_extra)
        return decision

    except Exception:
        return {"error": "server_error", "action": "rest"}


@app.route("/agent/think", methods=["POST"])
def agent_think():
    """Legacy HTTP think endpoint. Now unused by the server-authoritative
    engine (which calls run_agent_decision directly), but kept functional."""
    data = request.get_json(force=True) or {}
    return jsonify(run_agent_decision(data))


# --- Server-authoritative SimEngine wiring (Phases 2-6) ---
# AVAILABLE_ACTIONS: the full action superset the engine advertises to the model
# (mirrors AVAILABLE_ACTIONS in index.html). normalize_decision still filters.
# Reuses the module-level lm_complete() and the MemoryStore `memory_store`
# instance already defined above.
AVAILABLE_ACTIONS = list(DECISION_ACTIONS)

# Import the engine module whether server.py is run as a script (cwd-relative)
# or imported as simulation.server (package-relative).
import sys as _sys  # noqa: E402
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sim_engine as _sim_engine  # noqa: E402

# Phase D (TECH_TREE_ENABLED) prompt/schema amendments, applied only when the
# engine flag is on so that flag-off prompts and request payloads stay
# byte-identical to Phase C. The blueprint schema line teaches the optional
# "tier" field; the "verdict" decision field carries the elder's comparative
# council judgment (approve-the-best + reject-the-rest-with-reasons in one
# decision -- the engine's COUNCIL VERDICT nudge explains when to use it).
# Bug fix (found by the Phase D live smoke): SEED_PROJECT_IDS was a hardcoded
# subset that never included the granary (or now the forge), so a blueprint
# with id "granary" validated and its approval OVERWROTE the seed registry
# entry (cheaper needs, wrong tier). Protect every seed template id.
SEED_PROJECT_IDS.update(_sim_engine.PROJECT_TEMPLATES.keys())

if _sim_engine.TECH_TREE_ENABLED:
    DECISION_SCHEMA["properties"]["verdict"] = {
        "type": ["object", "null"],
        "properties": {"rejections": {"type": "object"}},
    }
    DECISION_SCHEMA["properties"]["blueprint"]["properties"]["tier"] = {
        "type": ["integer", "null"],
    }
    # The corresponding SYSTEM_PROMPT/SYSTEM_PROMPT_SLIM rewrite (documenting
    # the optional "tier" field) now lives in prompts.py, applied at that
    # module's import time (which happens before this point, near the top of
    # this file) -- see prompts.py for the rewrite + its own startup sha256
    # print. Nothing to do here except the DECISION_SCHEMA amendments above.


def _llm_decide(payload):
    """Engine -> LM bridge: run the existing decision pipeline + log it."""
    return run_agent_decision(payload)


_ENGINE_DEPS = {
    "ROLES": ROLES,
    "ROLE_PROJECT": ROLE_PROJECT,
    "ROLE_SKILLS": {role: d.get("skill", "helps the village") for role, d in ROLES.items()},
    "ROLE_PRIMARY_RESOURCE": ROLE_PRIMARY_RESOURCE,
    "RESOURCE_GATHER_ROLES": RESOURCE_GATHER_ROLES,
    "AVAILABLE_ACTIONS": AVAILABLE_ACTIONS,
    "SLUG_RE": SLUG_RE,
    "llm_decide": _llm_decide,
    "lm_complete": lm_complete,
    "is_scaffold_text": is_scaffold_text,
    "memory_store": memory_store,
    "log_activity": session_logger.log_activity,
    "log_conversation": session_logger.log_conversation,
    "log_benchmark": session_logger.log_benchmark,
    "flush_benchmarks": session_logger.flush_benchmarks,
    "log_divine": session_logger.log_divine,
    "log_compiler": session_logger.log_compiler,
    "validate_blueprint": validate_blueprint,
    "validate_sprite_block": validate_sprite_block,
    "sprite_spec_is_degenerate": sprite_spec_is_degenerate,
    "SPRITE_GRID_MIN": SPRITE_GRID_MIN,
    "SPRITE_GRID_MAX": SPRITE_GRID_MAX,
    "canonical_effect_vector": canonical_effect_vector,
    "run_piano_module": run_piano_module,
    "run_meta_update": run_meta_update,
    "normalize_decision": normalize_decision,
    "synthesize_divine_response": synthesize_divine_response,
    "build_agent_data": build_agent_data,
}

_roster_env = os.environ.get("SIM_AGENTS")
try:
    _roster_size = int(_roster_env) if _roster_env else 8
except ValueError:
    _roster_size = 8

engine = _sim_engine.SimEngine(_ENGINE_DEPS, roster_size=_roster_size)

# Full-state resume (Contract 3): if a valid state.db exists, rehydrate the
# world (frameTick, civilization, agents, re-embedded memory) instead of using
# the cold-start roster the constructor just built. Otherwise keep cold start.
if engine.restore_state():
    print(f"[server] resumed from state.db @ frameTick={engine.frameTick} "
          f"(level {engine.civilization['level']}, "
          f"{len(engine.civilization['structures'])} structures, "
          f"memory {memory_store.size()})")
else:
    print("[server] cold start (no valid state.db)")


# Per-file (min_frame, max_frame) for llm.jsonl — keyed by absolute path,
# invalidated by mtime+size so /council-llm-log can skip out-of-range files
# without a full council-filter parse.
_COUNCIL_LLM_FRAME_BOUNDS_CACHE = {}


def _cache_llm_jsonl_frame_bounds(path, min_frame, max_frame):
    try:
        st = os.stat(path)
    except OSError:
        return
    _COUNCIL_LLM_FRAME_BOUNDS_CACHE[path] = {
        "mtime": st.st_mtime,
        "size": st.st_size,
        "min_frame": min_frame,
        "max_frame": max_frame,
    }


def _llm_jsonl_frame_bounds(path):
    """Return (min_frame, max_frame) for type=llm records, or (None, None)."""
    if not os.path.isfile(path):
        return None, None
    try:
        st = os.stat(path)
    except OSError:
        return None, None
    cached = _COUNCIL_LLM_FRAME_BOUNDS_CACHE.get(path)
    if (cached
            and cached["mtime"] == st.st_mtime
            and cached["size"] == st.st_size):
        return cached["min_frame"], cached["max_frame"]
    min_frame, max_frame = None, None
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "llm":
                    continue
                ft = rec.get("frame_tick")
                if ft is None:
                    continue
                if min_frame is None or ft < min_frame:
                    min_frame = ft
                if max_frame is None or ft > max_frame:
                    max_frame = ft
    except OSError:
        return None, None
    _cache_llm_jsonl_frame_bounds(path, min_frame, max_frame)
    return min_frame, max_frame


def _llm_frame_window_fully_covered(min_frame, max_frame, start_frame, end_frame):
    if min_frame is None or max_frame is None:
        return False
    return min_frame <= start_frame and end_frame <= max_frame


def _council_llm_entries_from_file(path, start_frame, end_frame, agent_set):
    """Return slim decision records from one llm.jsonl matching a council
    frame window. Shared by /council-llm-log -- see that route's docstring
    for the filtering rules."""
    entries, _, _ = _scan_council_llm_file(path, start_frame, end_frame, agent_set)
    return entries


def _scan_council_llm_file(path, start_frame, end_frame, agent_set):
    """One pass over llm.jsonl: council-filtered entries plus file bounds."""
    if not os.path.isfile(path):
        return [], None, None
    entries = []
    min_frame, max_frame = None, None
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "llm":
                    continue
                ft = rec.get("frame_tick")
                if ft is not None:
                    if min_frame is None or ft < min_frame:
                        min_frame = ft
                    if max_frame is None or ft > max_frame:
                        max_frame = ft
                if ft is None or ft < start_frame or ft > end_frame:
                    continue
                name = rec.get("agent_name")
                if agent_set and name not in agent_set:
                    continue
                decision = rec.get("decision") or {}
                action = decision.get("action")
                invention_only = bool(rec.get("invention_only"))
                if not invention_only:
                    req = rec.get("request") or {}
                    for msg in req.get("messages") or []:
                        content = (msg.get("content") or "").lower()
                        if "invention-only" in content or "propose a new structure blueprint" in content:
                            invention_only = True
                            break
                is_verdict = isinstance(decision.get("verdict"), dict)
                if (action not in COUNCIL_LLM_ACTIONS
                        and not invention_only
                        and not is_verdict):
                    continue
                slim_decision = {
                    "action": action,
                    "reasoning": decision.get("reasoning"),
                    "message": decision.get("message"),
                    "verdict": decision.get("verdict"),
                    "blueprint_name": (decision.get("blueprint") or {}).get("name"),
                }
                entries.append({
                    "agent_name": name,
                    "frame_tick": ft,
                    "ts": rec.get("ts"),
                    "latency_ms": rec.get("latency_ms"),
                    "invention_only": invention_only,
                    "decision": slim_decision,
                    "error": rec.get("error"),
                })
    except OSError:
        return [], None, None
    _cache_llm_jsonl_frame_bounds(path, min_frame, max_frame)
    return entries, min_frame, max_frame


@app.route("/council-llm-log")
def council_llm_log():
    """Return slim decision records (llm.jsonl) for a council frame window.

    Only blueprint-pitch and verdict turns are included — routine gather/talk
    decisions from the same agents during the council window are omitted.

    Scans the live session's llm.jsonl first; only reads older retained
    session directories when the requested frame window is not fully covered
    by the live file's frame range (frame_tick is monotonic across restarts,
    but each session only spans the frames recorded while that server run was
    alive). Out-of-range files are skipped using cached per-file bounds when
    possible. Session dirs are pruned by SessionLogger._prune_old_sessions to
    LOG_RETENTION_SESSIONS newest."""
    try:
        start_frame = int(request.args.get("start_frame", 0))
        end_frame = int(request.args.get("end_frame", 0))
    except (TypeError, ValueError):
        return jsonify({"entries": [], "error": "invalid frame range"}), 400
    agents_raw = request.args.get("agents") or ""
    agent_set = {a.strip() for a in agents_raw.split(",") if a.strip()}
    logs_root = os.path.dirname(session_logger.dir)
    live_path = session_logger.llm_path
    entries, live_min, live_max = _scan_council_llm_file(
        live_path, start_frame, end_frame, agent_set)
    if not _llm_frame_window_fully_covered(
            live_min, live_max, start_frame, end_frame):
        try:
            session_dirs = sorted(
                name for name in os.listdir(logs_root)
                if SESSION_DIR_RE.match(name)
                and os.path.isdir(os.path.join(logs_root, name))
            )  # ISO session-id names sort lexicographically == chronologically
        except OSError:
            session_dirs = []
        for name in session_dirs:
            if name == session_logger.session_id:
                continue
            path = os.path.join(logs_root, name, "llm.jsonl")
            fmin, fmax = _llm_jsonl_frame_bounds(path)
            if fmin is not None and fmax is not None:
                if fmax < start_frame or fmin > end_frame:
                    continue
            entries.extend(_council_llm_entries_from_file(
                path, start_frame, end_frame, agent_set))
    entries.sort(key=lambda e: e.get("frame_tick") or 0)
    return jsonify({"entries": entries})


@app.route("/state")
def state():
    """Consistent world snapshot for the thin viewer (Contract 2)."""
    since_raw = request.args.get("since")
    since = None
    if since_raw is not None:
        try:
            since = int(since_raw)
        except (TypeError, ValueError):
            since = None
    if since is None:
        return jsonify(engine.snapshot())
    return jsonify(engine.snapshot_delta(since))


@app.route("/districts.js")
def districts_js():
    """Live districts/roads for the viewer (world-expansion plan). Despite the
    ".js" name (matching the plan's route naming), the body is plain JSON;
    the viewer fetch()-polls it rather than re-injecting a <script> tag.

    Under the engine lock, read districtsEpoch and either return a tiny
    unchanged body when ?since= matches, or shallow-copy district/road data
    into plain dicts/lists. JSON assembly happens after the lock is released."""
    since_raw = request.args.get("since")
    since = None
    if since_raw is not None:
        try:
            since = int(since_raw)
        except (TypeError, ValueError):
            since = None

    with engine.lock:
        epoch = engine.districtsEpoch
        if since is not None and since == epoch:
            payload = {"unchanged": True, "epoch": epoch}
        else:
            c = engine.civilization
            districts = [
                {"id": did, "kind": d["kind"], "tile": d["tile"], "label": d.get("label"),
                 "bounds": dict(d["bounds"]),
                 "buildGrid": dict(d["build_grid"]) if d.get("build_grid") else None,
                 "tiles": dict(d.get("tiles") or {}),
                 "terrain": dict(d.get("terrain") or {}),
                 "settlementId": d.get("settlementId")}
                for did, d in c["districts"].items()
            ]
            road_nodes = {nid: dict(n) for nid, n in c["roadNodes"].items()}
            road_edges = [list(e) for e in c["roadEdges"]]
            payload = {
                "districts": districts,
                "roadNodes": road_nodes,
                "roadEdges": road_edges,
                "epoch": epoch,
            }
    return jsonify(payload)


@app.route("/control/pause", methods=["POST"])
def control_pause():
    engine.pause()
    return jsonify({"ok": True, "paused": True})


@app.route("/control/resume", methods=["POST"])
def control_resume():
    engine.resume()
    return jsonify({"ok": True, "paused": False})


_RESET_PASSWORD_RAW = os.environ.get("SIM_RESET_PASSWORD", "").strip()
RESET_PASSWORD = _RESET_PASSWORD_RAW if _RESET_PASSWORD_RAW else "reset"


@app.route("/control/reset", methods=["POST"])
def control_reset():
    body = request.get_json(force=True, silent=True) or {}
    supplied = body.get("password", "")
    if not isinstance(supplied, str):
        supplied = ""
    if not hmac.compare_digest(supplied, RESET_PASSWORD):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    agents = body.get("agents")
    try:
        agents = int(agents) if agents else None
    except (TypeError, ValueError):
        agents = None
    engine.reset(roster_size=agents)
    return jsonify({"ok": True, "agents": engine.roster_size})


# --- Sovereign God mode (docs/plan-sovereign-god-mode-v2.md, Phase 2) ---
# Two-gate model: GOD_MODE_ENABLED (sim_engine.py) is always required;
# GOD_AUTH_REQUIRED (sim_engine.py, default False) optionally requires a
# token. GOD_TOKEN is read ONCE at import here -- no route may change any of
# these, and there is no live on/off switch by design. Routes go live when the
# flag is on AND either auth is off OR a non-empty token is configured. A
# flag-on/auth-required/token-missing combination is a misconfiguration,
# reported once at startup WITHOUT the secret itself (there is none to reveal
# -- the token is simply absent), and every /control/god/* route stays
# disabled until a restart supplies one.
GOD_TOKEN = os.environ.get("SIM_GOD_TOKEN", "").strip()
GOD_ROUTES_ACTIVE = (
    _sim_engine.GOD_MODE_ENABLED
    and (bool(GOD_TOKEN) or not _sim_engine.GOD_AUTH_REQUIRED)
)
if _sim_engine.GOD_MODE_ENABLED and not _sim_engine.GOD_AUTH_REQUIRED:
    _god_bind_host = os.environ.get("SIM_HOST", "0.0.0.0")
    print("[server] SECURITY: God API is unauthenticated (SIM_GOD_AUTH off); "
          f"listening bind will be {_god_bind_host} — any LAN client can "
          "mutate the world via /control/god/*.")
elif _sim_engine.GOD_MODE_ENABLED and _sim_engine.GOD_AUTH_REQUIRED and not GOD_TOKEN:
    print("[server] WARNING: SIM_GOD_MODE is enabled but SIM_GOD_TOKEN is unset/blank -- "
          "every /control/god/* route stays disabled until a token is configured "
          "and the server is restarted.")

# A proclamation payload is small; this bounds request bodies well above any
# legitimate use while still capping abuse. Checked before request.get_json
# so an oversized body never reaches JSON parsing.
GOD_MAX_BODY_BYTES = 8192


def _god_authorized():
    """True when God routes are active and, if GOD_AUTH_REQUIRED, the
    request's X-God-Token header matches via constant-time comparison."""
    if not GOD_ROUTES_ACTIVE:
        return False
    if not _sim_engine.GOD_AUTH_REQUIRED:
        return True
    supplied = request.headers.get("X-God-Token", "")
    return hmac.compare_digest(supplied, GOD_TOKEN)


def _god_unauthorized_response():
    """ONE uniform shape for every authorization failure -- disabled flag,
    missing token config, missing header, and wrong token are all
    indistinguishable from the outside, and no God route ever reveals
    whether a target/event exists to an unauthorized caller."""
    return jsonify({"error": "unauthorized"}), 401


def _god_body_too_large():
    length = request.content_length
    return isinstance(length, int) and length > GOD_MAX_BODY_BYTES


@app.route("/control/god/capabilities", methods=["GET"])
def control_god_capabilities():
    if not _god_authorized():
        return _god_unauthorized_response()
    return jsonify({
        "ok": True,
        "godModeEnabled": _sim_engine.GOD_MODE_ENABLED,
        "tokenConfigured": bool(GOD_TOKEN),
        "kinds": {
            "proclamation": {
                "applyable": True,
                "payload": {"text": {"type": "string",
                                     "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                                     "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES}},
                "reversibilityClass": "irreversible",
                "notes": (
                    "Chronicle/activity proclamation plus auto-applies as timed "
                    "providence (same text, default duration, replace slot)."
                ),
            },
            # Sovereign God mode Phase 3 (docs/plan-sovereign-god-mode-v2.md
            # "Voice and providence").
            "providence": {
                "applyable": True,
                "payload": {
                    "text": {"type": "string", "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                             "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                },
                "reversibilityClass": "cancellable",
            },
            "private_omen": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "text": {"type": "string", "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                             "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                },
                "reversibilityClass": "cancellable",
            },
            # Divine Matrix Phase 1: batch whisper campaign (private omens per target).
            "whisper_campaign": {
                "applyable": True,
                "payload": {
                    "theme": {"type": "string", "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                              "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                    "whispers": {
                        "type": "array",
                        "maxItems": _sim_engine.GOD_WHISPER_CAMPAIGN_MAX_TARGETS,
                        "items": {
                            "type": "object",
                            "properties": {
                                "targetId": {"type": "integer"},
                                "text": {"type": "string",
                                         "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                                         "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES},
                            },
                        },
                    },
                },
                "reversibilityClass": "cancellable",
            },
            # Divine Matrix Phase 2: per-agent LLM sampling overlay (private).
            "agent_sampling": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "model": {"type": "string", "enum": list(_sim_engine.GOD_AGENT_SAMPLING_MODELS),
                              "optional": True, "default": "sim-smart"},
                    "temperature": {"type": "number",
                                    "min": _sim_engine.GOD_AGENT_SAMPLING_TEMP_MIN,
                                    "max": _sim_engine.GOD_AGENT_SAMPLING_TEMP_MAX},
                    "top_p": {"type": "number", "optional": True,
                              "min": _sim_engine.GOD_AGENT_SAMPLING_TOP_P_MIN,
                              "max": _sim_engine.GOD_AGENT_SAMPLING_TOP_P_MAX},
                    "top_k": {"type": "integer", "optional": True,
                              "min": _sim_engine.GOD_AGENT_SAMPLING_TOP_K_MIN,
                              "max": _sim_engine.GOD_AGENT_SAMPLING_TOP_K_MAX},
                    "min_p": {"type": "number", "optional": True,
                              "min": _sim_engine.GOD_AGENT_SAMPLING_MIN_P_MIN,
                              "max": _sim_engine.GOD_AGENT_SAMPLING_MIN_P_MAX},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                },
                "reversibilityClass": "cancellable",
                "notes": (
                    f"at most {_sim_engine.GOD_AGENT_SAMPLING_FAST_DECISION_CAP} living agent "
                    "may use sim-fast for decisions at once (PIANO pool contention)."
                ),
            },
            "revoke_agent_sampling": {
                "applyable": True,
                "payload": {"targetId": {"type": "integer"}},
                "reversibilityClass": "cancellable",
            },
            # Divine Matrix Phase 3: memory surgery (private, irreversible).
            "memory_insert": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "text": {"type": "string", "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                             "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES},
                    "salience": {"type": "number", "min": 0.0, "max": 1.0,
                                 "optional": True, "default": 0.7},
                    "kind": {"type": "string", "optional": True,
                             "default": _sim_engine.GOD_MEMORY_DEFAULT_KIND,
                             "maxLen": _sim_engine.GOD_MEMORY_KIND_MAX_LEN},
                },
                "reversibilityClass": "irreversible",
                "notes": "never in public activity/chronicle; outcomes omit memory text.",
            },
            "memory_delete": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "keyword": {"type": "string", "optional": True},
                    "frameFrom": {"type": "integer", "optional": True},
                    "frameTo": {"type": "integer", "optional": True},
                    "kinds": {"type": "array", "optional": True, "items": {"type": "string"}},
                },
                "reversibilityClass": "irreversible",
                "notes": "at least one of keyword/frameFrom/frameTo/kinds required; "
                         "outcome is deletedCount only.",
            },
            "belief_plant": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "beliefId": {"type": "string", "optional": True},
                    "text": {"type": "string", "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                             "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES, "optional": True},
                    "plantInMemeTexts": {"type": "boolean"},
                    "salience": {"type": "number", "min": 0.0, "max": 1.0,
                                 "optional": True, "default": 0.7},
                },
                "reversibilityClass": "irreversible",
                "notes": "at least one of beliefId or text; never in public logs.",
            },
            # Divine Matrix Phase 4: reality distortion / context masks (private).
            "context_mask": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "mode": {"type": "string",
                             "enum": sorted(_sim_engine.GOD_CONTEXT_MASK_MODES)},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                    "dreamSnapshot": {
                        "type": "object", "optional": True,
                        "allowedKeys": sorted(_sim_engine.GOD_CONTEXT_MASK_DREAM_KEYS),
                        "notes": "required when mode=dream; unknown keys rejected",
                    },
                    "forgedConversations": {
                        "type": "array", "optional": True,
                        "maxItems": _sim_engine.GOD_CONTEXT_MASK_FORGED_MAX,
                        "notes": "required when mode=whisper_chain",
                    },
                },
                "reversibilityClass": "cancellable",
                "notes": "mutates think payload only; cancel via god_cancel on mask id.",
            },
            # Divine Matrix Phase 5: decision gate / possession pipeline (private).
            "decision_compulsion": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "pinnedDecision": {"type": "object", "notes": "must include action; validated via normalize_decision"},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES},
                    "remainingTurns": {"type": "integer", "optional": True, "min": 1},
                },
                "reversibilityClass": "cancellable",
                "notes": "at least one of durationFrames or remainingTurns required; cancel via god_cancel.",
            },
            "decision_veto_arm": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                },
                "reversibilityClass": "cancellable",
            },
            "decision_veto_resolve": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "resolution": {"type": "string", "enum": ["approve", "reject", "rewrite"]},
                    "rewrittenDecision": {"type": "object", "optional": True},
                },
                "reversibilityClass": "irreversible",
            },
            "agent_possession": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "pinnedDecision": {"type": "object", "optional": True},
                    "queue": {"type": "array", "optional": True, "maxItems": 8},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                },
                "reversibilityClass": "cancellable",
                "notes": "skips LLM when active; pinnedDecision or queue required.",
            },
            "revoke_decision_gate": {
                "applyable": True,
                "payload": {"targetId": {"type": "integer"}},
                "reversibilityClass": "cancellable",
            },
            # Divine Matrix Phase 6: Burning Bush + Merovingian Bargain (private).
            "burning_bush_message": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "text": {"type": "string", "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                             "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES},
                },
                "reversibilityClass": "cancellable",
                "notes": "private thread; never in /state; Sight shows messageCount only.",
            },
            "burning_bush_close": {
                "applyable": True,
                "payload": {"targetId": {"type": "integer"}},
                "reversibilityClass": "cancellable",
            },
            "merovingian_bargain": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "termsText": {"type": "string", "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                                  "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES},
                    "successPredicate": {
                        "type": "object",
                        "kindEnum": sorted(_sim_engine.GOD_BARGAIN_PREDICATES),
                    },
                    "failurePredicate": {
                        "type": "object", "optional": True,
                        "kindEnum": sorted(_sim_engine.GOD_BARGAIN_PREDICATES),
                    },
                    "rewardPrimitive": {
                        "type": "object", "optional": True,
                        "kindEnum": sorted(_sim_engine.GOD_BARGAIN_PRIMITIVE_KINDS),
                    },
                    "punishPrimitive": {
                        "type": "object", "optional": True,
                        "kindEnum": sorted(_sim_engine.GOD_BARGAIN_PRIMITIVE_KINDS),
                    },
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                },
                "reversibilityClass": "cancellable",
                "notes": "predicates allowlisted only; auto-settle on tick; grant rewards are public.",
            },
            "bargain_settle": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "outcome": {"type": "string", "enum": ["success", "failure"]},
                },
                "reversibilityClass": "irreversible",
                "notes": "manual settle; tick auto-settle is primary path.",
            },
            # Divine Matrix Phase 7: Anointed (destiny private, stigmata in neighbor prompts).
            "anoint": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "destinyText": {"type": "string",
                                    "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                                    "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES},
                    "stigmataTags": {
                        "type": "array", "optional": True,
                        "maxItems": _sim_engine.GOD_ANOINT_STIGMATA_MAX,
                        "items": {"type": "string",
                                  "maxChars": _sim_engine.GOD_ANOINT_STIGMATA_TAG_MAX_CHARS},
                    },
                    "oracleHints": {
                        "type": "array", "optional": True,
                        "maxItems": _sim_engine.GOD_ANOINT_ORACLE_HINTS_MAX,
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string",
                                         "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                                         "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES},
                                "revealFrame": {"type": "integer", "min": 0},
                            },
                        },
                    },
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                },
                "reversibilityClass": "cancellable",
                "notes": "destiny/oracle private; stigmata in nearby-agent prompt only; "
                         "cancel via god_cancel or revoke_anoint.",
            },
            "revoke_anoint": {
                "applyable": True,
                "payload": {"targetId": {"type": "integer"}},
                "reversibilityClass": "cancellable",
            },
            # Divine Matrix Phase 8: Identity Forge (persona/personality/role).
            "identity_edit": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "persona": {
                        "type": "string", "optional": True,
                        "maxChars": _sim_engine.GOD_IDENTITY_PERSONA_MAX_CHARS,
                    },
                    "personality": {
                        "type": "string", "optional": True,
                        "maxChars": _sim_engine.GOD_IDENTITY_PERSONALITY_MAX_CHARS,
                        "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES,
                    },
                    "role": {"type": "string", "optional": True,
                             "description": "must exist in roles.json"},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                },
                "reversibilityClass": "cancellable",
                "notes": "timed edits restore snapshot on expiry/cancel; permanent edits are consequential.",
            },
            "identity_copy_overwrite": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "sourceId": {"type": "integer"},
                    "ratePerThink": {"type": "number", "min": 0.0, "max": 1.0},
                    "syncMemories": {"type": "boolean", "optional": True, "default": False},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                },
                "reversibilityClass": "cancellable",
                "notes": "blends persona/personality each think; optional syncMemories plants up to 3 source memories.",
            },
            "identity_forge_cancel": {
                "applyable": True,
                "payload": {"targetId": {"type": "integer"}},
                "reversibilityClass": "irreversible",
                "notes": "restores snapshot taken at apply; clears active forge.",
            },
            # Divine Matrix Phase 9: Architect Zones (paint / door / limbo).
            "architect_zone": {
                "applyable": True,
                "payload": {
                    "zoneKind": {"type": "string", "enum": ["paint", "door", "limbo"]},
                    "districtId": {"type": "string", "optional": True},
                    "cells": {
                        "type": "array",
                        "maxItems": _sim_engine.GOD_ARCHITECT_ZONE_MAX_CELLS,
                        "description": 'gx,gy strings or {gx1,gy1,gx2,gy2} bounds',
                    },
                    "paintTerrain": {
                        "type": "string", "optional": True,
                        "enum": sorted(_sim_engine.GOD_ARCHITECT_PAINT_TERRAINS),
                    },
                    "keyId": {"type": "string", "optional": True,
                              "maxChars": _sim_engine.GOD_ARCHITECT_KEY_MAX_LEN},
                    "grantKeyAgentIds": {"type": "array", "optional": True,
                                         "items": {"type": "integer"}},
                    "holdAgentIds": {"type": "array", "optional": True,
                                     "items": {"type": "integer"}},
                    "reversible": {"type": "boolean", "optional": True, "default": True},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                },
                "reversibilityClass": "cancellable",
                "notes": "paint is public/world-visible; door/limbo audit private. grantKeyAgentIds grants godKeys tags on apply.",
            },
            "architect_zone_cancel": {
                "applyable": True,
                "payload": {"zoneId": {"type": "string"}},
                "reversibilityClass": "cancellable",
            },
            "architect_release_hold": {
                "applyable": True,
                "payload": {
                    "zoneId": {"type": "string"},
                    "agentIds": {"type": "array", "optional": True,
                                 "items": {"type": "integer"}},
                },
                "reversibilityClass": "cancellable",
            },
            # Divine Matrix Phase 10: Reload / Déjà Vu checkpoints.
            "checkpoint_create": {
                "applyable": True,
                "payload": {
                    "label": {"type": "string", "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                              "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES},
                    "replaceOldest": {"type": "boolean", "optional": True, "default": False},
                },
                "reversibilityClass": "irreversible",
                "notes": (
                    f"cap {_sim_engine.GOD_CHECKPOINT_MAX} checkpoints; "
                    "reject at preview when full unless replaceOldest is true."
                ),
            },
            "checkpoint_restore": {
                "applyable": True,
                "payload": {"checkpointId": {"type": "string"}},
                "reversibilityClass": "irreversible",
                "notes": "irreversible world replace — copies checkpoint state.db + memory_store.json, then restore_state().",
            },
            "deja_vu_replay": {
                "applyable": _sim_engine.GOD_DEJA_VU_REPLAY,
                "payload": {
                    "targetId": {"type": "integer"},
                    "maxSteps": {
                        "type": "integer", "optional": True,
                        "min": 1, "max": _sim_engine.GOD_DEJA_VU_MAX_STEPS,
                        "default": _sim_engine.GOD_DEJA_VU_MAX_STEPS,
                    },
                },
                "reversibilityClass": "cancellable",
                "sessionCap": _sim_engine.GOD_DEJA_VU_SESSION_CAP,
                "notes": (
                    f"sequences up to {_sim_engine.GOD_DEJA_VU_MAX_STEPS} compulsion "
                    "steps from decisionDigests for one agent; cancel parent clears "
                    "remaining gates."
                ),
            },
            "crowd_compulsion": {
                "applyable": True,
                "payload": {
                    "theme": {
                        "type": "string", "optional": True,
                        "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                        "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES,
                    },
                    "durationFrames": {
                        "type": "integer", "optional": True,
                        "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                        "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                    },
                    "remainingTurns": {"type": "integer", "optional": True, "min": 1},
                    "targets": {
                        "type": "array",
                        "maxItems": _sim_engine.GOD_CROWD_COMPULSION_MAX_TARGETS,
                        "items": {
                            "type": "object",
                            "properties": {
                                "targetId": {"type": "integer"},
                                "pinnedDecision": {
                                    "type": "object",
                                    "notes": "must include action; validated via normalize_decision",
                                },
                            },
                        },
                    },
                },
                "reversibilityClass": "cancellable",
                "notes": (
                    "at least one of durationFrames or remainingTurns required; "
                    "cancel parent id clears all linked gates."
                ),
            },
            "dream_broadcast": {
                "applyable": True,
                "payload": {
                    "durationFrames": {
                        "type": "integer",
                        "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                        "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                        "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES,
                    },
                    "dreamSnapshot": {
                        "type": "object",
                        "notes": "required; keys validated per GOD_CONTEXT_MASK_DREAM_KEYS",
                    },
                    "targetIds": {
                        "type": "array",
                        "maxItems": _sim_engine.GOD_DREAM_BROADCAST_MAX_TARGETS,
                        "items": {"type": "integer"},
                    },
                },
                "reversibilityClass": "cancellable",
                "notes": "shared dream snapshot per target; cancel parent clears all linked masks.",
            },
            "revoke_guidance": {
                "applyable": True,
                "payload": {"id": {"type": "string"}},
                "reversibilityClass": "irreversible",
            },
            # Sovereign God mode Phase 4 (docs/plan-sovereign-god-mode-v2.md
            # "Immediate miracles"). All three are irreversible.
            "agent_vitals": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "healthDelta": {"type": "number", "optional": True,
                                    "min": -_sim_engine.GOD_VITALS_DELTA_MAX,
                                    "max": _sim_engine.GOD_VITALS_DELTA_MAX},
                    "hungerDelta": {"type": "number", "optional": True,
                                    "min": -_sim_engine.GOD_VITALS_DELTA_MAX,
                                    "max": _sim_engine.GOD_VITALS_DELTA_MAX},
                },
                "reversibilityClass": "irreversible",
                "notes": ("cannot kill: a negative healthDelta is clamped to stop at "
                          f"{_sim_engine.GOD_VITALS_HEALTH_FLOOR} (never reaching the "
                          "incapacitation threshold), never touches deathFrame."),
            },
            "grant_resource": {
                "applyable": True,
                "payload": {
                    "resourceId": {"type": "string"},
                    "amount": {"type": "integer", "min": 1,
                              "max": _sim_engine.GOD_GRANT_PER_COMMAND_CAP},
                    "target": {"type": "object", "optional": True,
                              "description": '"stockpile" (default) or {"agentId": <int>}'},
                },
                "reversibilityClass": "irreversible",
                "sessionCap": _sim_engine.GOD_GRANT_SESSION_CAP,
            },
            "structure_condition": {
                "applyable": True,
                "payload": {
                    "structureId": {"type": "integer"},
                    "delta": {"type": "number",
                             "min": -_sim_engine.GOD_STRUCTURE_DELTA_MAX,
                             "max": _sim_engine.GOD_STRUCTURE_DELTA_MAX},
                },
                "reversibilityClass": "irreversible",
                "notes": "positive delta repairs, negative delta damages (may reach ruin).",
            },
            "repair_structures": {
                "applyable": True,
                "payload": {
                    "scope": {"type": "string | object",
                              "enum": ["ids", "all_critical"],
                              "description": '"ids", "all_critical", or {"districtId": "<id>"}'},
                    "structureIds": {"type": "array", "optional": True,
                                    "description": "required when scope is ids"},
                    "conditionTarget": {"type": "number", "optional": True,
                                       "min": 0, "max": 100},
                    "unRuin": {"type": "boolean", "optional": True, "default": True},
                },
                "reversibilityClass": "irreversible",
                "batchMax": _sim_engine.GOD_REPAIR_STRUCTURES_BATCH_MAX,
                "conditionMax": _sim_engine.GOD_REPAIR_STRUCTURES_CONDITION_MAX,
                "notes": "batch restore / un-ruin; only this command and agent repair_structure may un-ruin.",
            },
            "clear_ruins": {
                "applyable": True,
                "payload": {
                    "structureIds": {"type": "array", "optional": True},
                    "minAgeFrames": {"type": "integer", "optional": True,
                                    "default": _sim_engine.RUIN_CULL_AGE_FRAMES},
                    "districtId": {"type": "string", "optional": True},
                },
                "reversibilityClass": "irreversible",
                "batchMax": _sim_engine.GOD_CLEAR_RUINS_BATCH_MAX,
                "notes": "delete selected or aged ruins; mirrors engine cull cleanup.",
            },
            # Sovereign God mode Phase 5 (docs/plan-sovereign-god-mode-v2.md
            # "Storyteller events" + "Timed lawgiver modifiers"). Reversibility
            # is "cancellable" with no primitives, "consequential" once any
            # primitive is included (see _god_reversibility_class).
            "story_event": {
                "applyable": True,
                "payload": {
                    "title": {"type": "string", "maxChars": _sim_engine.GOD_EVENT_TITLE_MAX_CHARS},
                    "narration": {"type": "string", "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                                 "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES},
                    "visibility": {"type": "string", "optional": True,
                                  "enum": ["public", "private"], "default": "public"},
                    "targetId": {"type": "integer", "optional": True,
                                "description": "required when visibility is 'private'"},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                    "modifiers": {"type": "object", "optional": True,
                                 "keys": _sim_engine.GOD_MODIFIER_RANGES},
                    "primitives": {"type": "array", "optional": True,
                                  "maxItems": _sim_engine.GOD_STORY_EVENT_MAX_PRIMITIVES,
                                  "itemKinds": ["agent_vitals", "grant_resource", "structure_condition"]},
                    "providence": {"type": "object", "optional": True,
                                  "description": "{text} -- reuses this event's own durationFrames"},
                    "replaceEffectId": {"type": "string", "optional": True,
                                        "description": "required to reuse a modifier key already active"},
                },
                "reversibilityClass": "cancellable | consequential (with primitives)",
                "notes": "atomic: preview validates every component; apply accepts all or changes nothing.",
            },
            # Sovereign God mode Phase 6 (docs/plan-sovereign-god-mode-v2.md
            # "Weather override"). Always "consequential": entering "storm"
            # can permanently damage structures in the named districts, and
            # neither cancelling nor natural expiry undoes that damage.
            "weather_override": {
                "applyable": True,
                "requires": "WEATHER_ENABLED",
                "payload": {
                    "state": {"type": "string", "enum": list(_sim_engine.WEATHER_STATES)},
                    "districts": {"type": "array", "optional": True,
                                 "description": ('district ids; empty ONLY for state="clear"; '
                                                 "at least one required for every other state")},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                    "replaceEffectId": {"type": "string", "optional": True,
                                        "description": "required to replace the already-active weather override"},
                },
                "reversibilityClass": "consequential",
                "notes": ('clock ownership: activeEvents[].expiresFrame == weather["exitFrame"] '
                         "(the divine event owns duration). Cancelling or expiry hands off to the "
                         "natural cycle's next state -- never restores the prior state. Entering "
                         "'storm' can permanently damage structures in the named districts."),
            },
            # Huntable wildlife god kinds (specs/02-engine-core.md
            # "Sovereign God mode: wildlife kinds"). Irreversible one-shots
            # like grant_resource; gated on WILDLIFE_ENABLED.
            "wildlife_spawn": {
                "applyable": True,
                "requires": "WILDLIFE_ENABLED",
                "payload": {
                    "districtId": {"type": "string"},
                    "kind": {"type": "string",
                             "description": "must be valid for that district's habitat pool"},
                },
                "reversibilityClass": "irreversible",
                "kindPools": dict(_sim_engine.WILDLIFE_KIND_POOLS),
                "capPerDistrict": _sim_engine.WILDLIFE_CAP_PER_DISTRICT,
                "notes": "respects WILDLIFE_CAP_PER_DISTRICT; rejects unknown district/kind or full cap.",
            },
            "wildlife_despawn": {
                "applyable": True,
                "requires": "WILDLIFE_ENABLED",
                "payload": {
                    "id": {"type": "string", "optional": True,
                           "description": "creature id (mutually exclusive with districtId)"},
                    "districtId": {"type": "string", "optional": True,
                                   "description": "clear all alive fauna in district (mutually exclusive with id)"},
                },
                "reversibilityClass": "irreversible",
                "notes": "exactly one of id or districtId required; marks target(s) dead with ordinary respawn bookkeeping.",
            },
            "wildlife_set_hp": {
                "applyable": True,
                "requires": "WILDLIFE_ENABLED",
                "payload": {
                    "id": {"type": "string"},
                    "hp": {"type": "integer", "min": 0,
                           "description": "clamped to [0, maxHp]; hp<=0 kills with ordinary respawn bookkeeping"},
                },
                "reversibilityClass": "irreversible",
            },
        },
        "modifierRanges": _sim_engine.GOD_MODIFIER_RANGES,
        "previewTtlSeconds": _sim_engine.GOD_PREVIEW_TTL_SECONDS,
        "activeEventsCap": _sim_engine.GOD_ACTIVE_EVENTS_CAP,
        "weatherEnabled": _sim_engine.WEATHER_ENABLED,
        "wildlifeEnabled": _sim_engine.WILDLIFE_ENABLED,
        # Sovereign God mode Optional Phase 8 (docs/plan-sovereign-god-mode-
        # v2.md "Free-prose story compiler"): dual-gated on GOD_MODE_ENABLED
        # (already true to reach this route) AND the SEPARATE
        # GOD_COMPILER_ENABLED dark flag -- so the viewer can render or hide
        # the Compile tab correctly without probing /control/god/compile.
        "compiler": {
            "enabled": _sim_engine.GOD_MODE_ENABLED and _sim_engine.GOD_COMPILER_ENABLED,
            "minIntervalSec": _sim_engine.GOD_COMPILER_MIN_INTERVAL_SEC,
            "sessionCap": _sim_engine.GOD_COMPILER_SESSION_CAP,
            "promptMaxChars": _sim_engine.GOD_COMPILER_PROSE_MAX_CHARS,
        },
    })


@app.route("/control/god/sight", methods=["GET"])
def control_god_sight():
    if not _god_authorized():
        return _god_unauthorized_response()
    return jsonify(engine.god_sight())


@app.route("/control/god/preview", methods=["POST"])
def control_god_preview():
    if not _god_authorized():
        return _god_unauthorized_response()
    if _god_body_too_large():
        return jsonify({"error": "payload_too_large"}), 413
    envelope = request.get_json(force=True, silent=True) or {}
    return jsonify(engine.god_preview(envelope))


@app.route("/control/god/compile", methods=["POST"])
def control_god_compile():
    """Sovereign God mode Optional Phase 8 (docs/plan-sovereign-god-mode-
    v2.md "Free-prose story compiler"). Token-gated exactly like every other
    God route, but the token itself is NEVER forwarded to
    engine.god_compile_prose -- that method has no parameter for it and
    never reads SIM_GOD_TOKEN. This route only ever produces a PREVIEW; it
    never applies anything (there is no god_apply call anywhere on this
    path)."""
    if not _god_authorized():
        return _god_unauthorized_response()
    if _god_body_too_large():
        return jsonify({"error": "payload_too_large"}), 413
    body = request.get_json(force=True, silent=True) or {}
    prose = body.get("prose")
    if not isinstance(prose, str) or len(prose) > _sim_engine.GOD_COMPILER_PROSE_MAX_CHARS:
        return jsonify({"compileOk": False,
                        "reason": f"prose must be a string of at most "
                                  f"{_sim_engine.GOD_COMPILER_PROSE_MAX_CHARS} characters"})
    return jsonify(engine.god_compile_prose(prose))


@app.route("/control/god/apply", methods=["POST"])
def control_god_apply():
    if not _god_authorized():
        return _god_unauthorized_response()
    if _god_body_too_large():
        return jsonify({"error": "payload_too_large"}), 413
    body = request.get_json(force=True, silent=True) or {}
    return jsonify(engine.god_apply(body.get("previewId"), body.get("requestId")))


@app.route("/control/god/cancel", methods=["POST"])
def control_god_cancel():
    if not _god_authorized():
        return _god_unauthorized_response()
    if _god_body_too_large():
        return jsonify({"error": "payload_too_large"}), 413
    body = request.get_json(force=True, silent=True) or {}
    return jsonify(engine.god_cancel(body.get("targetId")))


if __name__ == "__main__":
    # Bind 0.0.0.0 so any device on the LAN can reach the sim (req #3); find this
    # machine's LAN IP with `ipconfig` and open the URL from another device as
    # http://<host-ip>:5001. On Windows, allow inbound TCP 5001 through the
    # firewall (or accept the first-run prompt). threaded=True lets the request
    # handlers run concurrently alongside the (forthcoming) SimEngine thread.
    # NOTE: this exposes the server — including the Ollama proxy and, when
    # GOD_AUTH_REQUIRED is off (the default), the unauthenticated God API —
    # to the whole local network. Intended for a trusted home LAN, not a
    # hostile network. Set SIM_GOD_AUTH=1 to restore token gating.
    HOST = os.environ.get("SIM_HOST", "0.0.0.0")
    PORT = int(os.environ.get("SIM_PORT", "5001"))
    # Start the server-authoritative engine thread BEFORE the HTTP server so the
    # world ticks headless regardless of any connected viewer.
    engine.start()
    print(f"[server] SimEngine started ({engine.roster_size} agents, "
          f"{_sim_engine.TICKS_PER_SEC} ticks/s)")

    # Graceful shutdown: flush the full state to disk on exit so a restart
    # resumes exactly. atexit covers normal exit; the signal handlers cover
    # Ctrl-C / `kill` (which otherwise bypass atexit during app.run()).
    _saved_once = threading.Event()

    def _flush_on_exit():
        if _saved_once.is_set():
            return
        _saved_once.set()
        session_logger.flush_benchmarks()
        engine.stop()
        engine.save_state(force=True)

    atexit.register(_flush_on_exit)

    def _signal_shutdown(signum, frame):
        _flush_on_exit()
        os._exit(0)

    for _sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(_sig, _signal_shutdown)
        except (ValueError, OSError):
            pass  # not in main thread / unsupported platform

    app.run(host=HOST, port=PORT, debug=False, threaded=True)
