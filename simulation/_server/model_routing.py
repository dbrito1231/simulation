"""Model routing / high-stakes-turn predicates, split out of server.py
(Phase 5 modularization, pure move, no behavior change).

Model routing (Phase 3 revision, 2026-07-24, docs/plan-ollama-migration.md):
the two-model split is by WORKLOAD KIND, not by decision stakes. ALL
decision turns (routine and high-stakes alike -- every call built by
build_decision_payload / run_agent_decision, both still in server.py) go to
MODEL_SMART. MODEL_FAST serves ONLY background cognition: PIANO modules
(run_piano_module), the memory summarizer/wiki merge, the meta system
(autobiography/persona), and belief-pitch scoring -- i.e. every direct
lm_complete() caller (all still in server.py). is_high_stakes_turn/
model_for_decision is UNCHANGED as a predicate: it still gates thinking
(THINKING_SAMPLING vs NON_THINKING_SAMPLING), timeouts (THINKING_TIMEOUT_S vs
DEFAULT_TIMEOUT_S), and max_tokens (HIGH_STAKES_MAX_TOKENS) -- those
sampling/timeout constants stay in server.py since they're only referenced
by build_decision_payload/run_agent_decision/lm_complete, not by anything
that moved here.

Rationale (superseding the original "routine decisions on MODEL_FAST"
design tried first): a live Phase 3 soak measured piano_module_drops
climbing to ~25-38% (vs the ~9% pre-migration reference) and module
latencies rising over the sample instead of falling, because routine
decisions and PIANO modules were both queueing for sim-fast's
OLLAMA_NUM_PARALLEL=3 slots -- decisions and background cognition were
contending for the same small model's capacity. Keeping ALL decisions on
sim-smart (which has its own, separate, uncontended slot pool) removes that
contention; sim-fast now serves only the background-cognition workload it
was sized for. Ids must be models created in Ollama (`ollama list` /
GET /api/tags) -- scripts/ollama_setup.py is the canonical loader that
creates/warms both. If a routed id isn't available, run_agent_decision
treats that as a setup failure (see looks_like_model_not_found_error) and
returns the offline fallback -- Ollama has no "local-model" alias to retry
with like LM Studio did, so there's no silent single-model degrade path
anymore.
"""

import threading
import time
from collections import deque

MODEL_SMART = "sim-smart"
MODEL_FAST = "sim-fast"

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
    never for decisions. See this module's docstring for the MODEL_SMART/
    MODEL_FAST split."""
    sampling = data.get("divine_sampling")
    if isinstance(sampling, dict):
        model_key = sampling.get("model")
        if model_key == "sim-fast":
            return MODEL_FAST
        if model_key == "sim-smart":
            return MODEL_SMART
    return MODEL_SMART
