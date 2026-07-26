"""Repeatable grounding screen for the production PIANO module prompts.

The screen intentionally parses ``MODULE_PROMPTS`` from ``simulation/server.py``
instead of importing that module: importing the server opens a log session and
constructs a live engine. The requests below mirror ``run_piano_module`` /
``lm_complete`` at the time they run: the production token budget is extracted
from ``server.py``; ``--max-tokens`` supplies a controlled comparison override.
The remaining settings are sim-fast, temperature 0.5, non-thinking sampling,
think:false, and a 15 second timeout.

Each synthetic case has explicit, inspectable checks.  This is a screen, not a
semantic judge: ``grounded-wrong`` means a contradicted claim or number matched
one of the case's stated checks.  ``invented-entity`` catches a capitalized
agent-like name absent from that case.  Truncation is exclusive so a token-limit
cutoff is not counted as a grounding failure.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "simulation" / "server.py"
SIMULATION_PATH = ROOT / "simulation"
if str(SIMULATION_PATH) not in sys.path:
    sys.path.insert(0, str(SIMULATION_PATH))

from llm_wire import to_ollama_body  # noqa: E402 -- path is configured above.


SCREEN_VERSION = "2026-07-25-v1"
DEFAULT_MODEL = "sim-fast"
DEFAULT_URL = "http://localhost:11434/api/chat"
MODULE_TEMPERATURE = 0.5
MODULE_TIMEOUT_S = 15
PIANO_CONCURRENT_LLM = 2
NON_THINKING_SAMPLING = {"top_p": 0.8, "top_k": 20, "min_p": 0}
EXPECTED_MODULES = ("perception", "social", "desire", "reflection")
CATEGORIES = ("grounded-wrong", "self-coordination", "invented-entity", "truncated", "clean")


@dataclass(frozen=True)
class ScreenCase:
    """One fixed context and the deterministic checks it makes possible."""

    case_id: str
    module: str
    agent: str
    role: str
    context: str
    allowed_names: tuple[str, ...]
    wrong_patterns: tuple[str, ...] = ()
    derived_numbers: tuple[str, ...] = ()


# Keep these contexts small, realistic, and fixed.  Their explicit negative
# checks are deliberately narrow: a match is a factual contradiction, while a
# non-match is not a claim that the response is generally wise.
CASES = (
    ScreenCase(
        "perception-clear-weather", "perception", "Mara", "scout",
        "role=scout; resources: food=4, wood=2; hunger=18; weather=clear; "
        "nearby: Toma (healthy, requests 2 wood); recent_memory: the river path is safe; "
        "no immediate danger is reported.",
        ("Mara", "Toma"), (r"\b(storm|wolf|fire|attack|immediate danger)\b",),
    ),
    ScreenCase(
        "perception-food-shortage", "perception", "Rowan", "gatherer",
        "role=gatherer; resources: food=0, berries=1; hunger=82; nearby: Nia (has 3 berries); "
        "recent_memory: Rowan skipped the last meal; weather=clear.",
        ("Rowan", "Nia"), (r"\b(plenty|enough|surplus)\s+(?:of )?(?:food|berries)\b",),
    ),
    ScreenCase(
        "perception-project-gap", "perception", "Ivo", "builder",
        "role=builder; resources: wood=1, stone=2; active_project=well needs wood=4 and stone=2; "
        "nearby: Sage (healthy); recent_memory: the project has not started because wood is short.",
        ("Ivo", "Sage"), (r"\b(?:well is complete|enough wood|wood requirement is met)\b",), ("3",),
    ),
    ScreenCase(
        "perception-completed-bridge", "perception", "Lina", "fisher",
        "role=fisher; resources: fish=3, wood=0; no active project; nearby: Brin (healthy); "
        "recent_memory: the bridge was completed yesterday and is usable.",
        ("Lina", "Brin"), (r"\b(?:build|construct|finish) (?:the )?bridge\b",),
    ),
    ScreenCase(
        "social-toma-wood-request", "social", "Mara", "scout",
        "role=scout; resources: wood=3, food=2; nearby: Toma (resources wood=0, message: \"Mara, "
        "could you spare 2 wood for the well?\"); relationship with Toma=ally; active_project=well needs 2 wood.",
        ("Mara", "Toma"),
        (r"\b(?:ask|request)\s+(?:Agent\s+)?Toma\b.{0,45}\bwood\b", r"\bToma\b.{0,45}\b(?:give|send|spare)\b.{0,25}\bwood\b"),
    ),
    ScreenCase(
        "social-nia-stone-offer", "social", "Rowan", "trader",
        "role=trader; resources: wheat=3, stone=0; nearby: Nia (resources stone=2, message: \"I will trade "
        "2 stone for Rowan's 3 wheat.\"); relationship with Nia=ally; active_project=well needs 2 stone.",
        ("Rowan", "Nia"),
        (r"\b(?:ask|request)\s+(?:Agent\s+)?Nia\b.{0,45}\bwheat\b", r"\bNia\b.{0,45}\b(?:give|send|offer)\b.{0,25}\bwheat\b"),
    ),
    ScreenCase(
        "social-nearby-patrol", "social", "Asha", "guard",
        "role=guard; resources: food=2; nearby: Brin (message: \"I will patrol the north path if you watch the "
        "market.\"); relationship with Brin=ally; no other nearby agents.",
        ("Asha", "Brin"), (),
    ),
    ScreenCase(
        "social-sage-blueprint", "social", "Dara", "builder",
        "role=builder; resources: wood=4, stone=1; nearby: Sage (message: \"Please review the granary blueprint.\"); "
        "relationship with Sage=ally; active_project=none.",
        ("Dara", "Sage"), (),
    ),
    ScreenCase(
        "desire-feed-village", "desire", "Nia", "farmer",
        "role=farmer; resources: wheat=5, food=0; village food=0; hunger=48; nearby: Mara (healthy); "
        "recent_memory: villagers skipped a meal; mill is available.",
        ("Nia", "Mara"), (r"\b(?:build|construct) (?:a |the )?bridge\b", r"\bmine stone\b"),
    ),
    ScreenCase(
        "desire-contribute-wood", "desire", "Ivo", "builder",
        "role=builder; resources: wood=4, stone=0; active_project=well needs wood=4 and stone=2; village stockpile wood=0; "
        "nearby: Sage; recent_memory: stone is already contributed.",
        ("Ivo", "Sage"), (r"\b(?:gather|collect|chop) wood\b", r"\bmine stone\b"),
    ),
    ScreenCase(
        "desire-heal-nia", "desire", "Eli", "healer",
        "role=healer; resources: medicine=1, food=1; nearby: Nia (health=22); village food=6; no active project; "
        "recent_memory: Nia was injured yesterday.",
        ("Eli", "Nia"), (r"\b(?:build|construct) (?:a |the )?bridge\b", r"\bmine stone\b"),
    ),
    ScreenCase(
        "desire-fish-for-food", "desire", "Lina", "fisher",
        "role=fisher; resources: fish=0, food=0; hunger=69; nearby: Brin (healthy); lake is nearby; no active project; "
        "recent_memory: fishing supplied the last meal.",
        ("Lina", "Brin"), (r"\b(?:build|construct) (?:a |the )?bridge\b", r"\bmine stone\b"),
    ),
    ScreenCase(
        "reflection-wood-for-fish", "reflection", "Mara", "scout",
        "role=scout; resources: food=1, wood=0; nearby: Toma; recent_memory: Toma exchanged 2 wood for 1 fish "
        "when Mara clearly stated the amount; current need=wood.",
        ("Mara", "Toma"), (r"\b(?:two|2) logs\b", r"\b(?:three|3) wood\b"),
    ),
    ScreenCase(
        "reflection-meal-reserve", "reflection", "Rowan", "gatherer",
        "role=gatherer; resources: berries=2, food=0; hunger=74; nearby: Nia; recent_memory: Rowan became hungry "
        "after giving away every berry; current need=food reserve.",
        ("Rowan", "Nia"), (r"\b25%\b", r"\b(?:give away|donate) every berr(?:y|ies)\b"),
    ),
    ScreenCase(
        "reflection-supply-before-build", "reflection", "Ivo", "builder",
        "role=builder; resources: wood=1, stone=0; nearby: Sage; recent_memory: the last well project stalled because "
        "Ivo started before its wood was gathered; active_project=well needs wood=4.",
        ("Ivo", "Sage"), (r"\b(?:start|build|construct) (?:the )?well (?:now|before|first)\b",),
    ),
    ScreenCase(
        "reflection-wheat-for-stone", "reflection", "Dara", "trader",
        "role=trader; resources: wheat=3, stone=0; nearby: Nia; recent_memory: Nia trades 2 stone for 3 wheat; "
        "current need=stone.",
        ("Dara", "Nia"), (r"\b(?:two|2) wheat\b.{0,30}\b(?:three|3) stone\b",),
    ),
)


# This is intentionally a conservative list of normal capitalized sentence
# words.  The candidate names left after it are included in JSON output, so a
# reviewer can inspect a new model's wording instead of trusting a black box.
NON_ENTITY_CAPITALS = {
    "A", "Agent", "An", "And", "Ask", "Avoid", "Based", "Build", "Catch", "Collect",
    "Contribute", "Coordinate", "Current", "Desire", "Fish", "Focus", "Food", "For",
    "From", "Gather", "Gathering", "Given", "Giving", "Goal", "Great", "Help", "If",
    "Immediate", "In", "It", "Learn", "Let", "Maintain", "Message", "No", "Offer", "One",
    "Opportunity", "Perception", "Prioritize", "Reach", "Recent", "Reflection", "Remember",
    "Request", "Social", "Stone", "Tell", "Thanks", "That", "The", "There", "This", "Threat",
    "Trade", "Use", "Village", "Wheat", "With", "Wood", "Work", "You",
}
NAME_RE = re.compile(r"\b[A-Z][a-z]{2,}\b")
NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?%?")
SELF_COORDINATION_RE_TEMPLATE = (
    r"\b(?:reach out to|coordinate|talk|speak|message|ask|request|trade|work)\s+"
    r"(?:with\s+|to\s+)?(?:agent\s+)?{agent}\b"
)


def load_module_prompts(server_path: Path = SERVER_PATH) -> dict[str, str]:
    """Read the literal prompt dictionary without importing side-effectful server.py."""

    tree = ast.parse(server_path.read_text(encoding="utf-8"), filename=str(server_path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "MODULE_PROMPTS" for target in node.targets):
            continue
        value = ast.literal_eval(node.value)
        if not isinstance(value, dict) or set(value) != set(EXPECTED_MODULES):
            raise ValueError("MODULE_PROMPTS must contain exactly " + ", ".join(EXPECTED_MODULES))
        if not all(isinstance(name, str) and isinstance(prompt, str) for name, prompt in value.items()):
            raise ValueError("MODULE_PROMPTS must be a string-to-string dictionary")
        return value
    raise ValueError(f"MODULE_PROMPTS assignment was not found in {server_path}")


def load_production_module_max_tokens(server_path: Path = SERVER_PATH) -> int:
    """Read the server's PIANO token budget without importing its side effects."""

    tree = ast.parse(server_path.read_text(encoding="utf-8"), filename=str(server_path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "PIANO_MODULE_MAX_TOKENS"
                   for target in node.targets):
            continue
        value = ast.literal_eval(node.value)
        if isinstance(value, int) and value > 0:
            return value
        raise ValueError("PIANO_MODULE_MAX_TOKENS must be a positive integer")
    raise ValueError("PIANO_MODULE_MAX_TOKENS assignment was not found in server.py")


def build_payload(prompt: str, case: ScreenCase, model: str, max_tokens: int) -> dict[str, Any]:
    """Mirror server.py's lm_complete payload for a PIANO module call."""

    return {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"You ARE {case.agent}. Context: {case.context}"},
        ],
        "max_tokens": max_tokens,
        "temperature": MODULE_TEMPERATURE,
        **NON_THINKING_SAMPLING,
        "think": False,
    }


def call_ollama(prompt: str, case: ScreenCase, model: str, url: str, timeout: float,
                max_tokens: int) -> dict[str, Any]:
    """Run one real native-Ollama call and retain fields needed for scoring."""

    payload = build_payload(prompt, case, model, max_tokens)
    try:
        response = requests.post(url, json=to_ollama_body(payload), timeout=timeout)
        response.raise_for_status()
        body = response.json()
        message = body.get("message") if isinstance(body, dict) else None
        text = message.get("content", "").strip() if isinstance(message, dict) else ""
        if not text:
            return {"error": "response missing message.content", "payload": payload, "body": body}
        return {
            "text": text,
            "done_reason": body.get("done_reason"),
            "eval_count": body.get("eval_count"),
            "payload": payload,
        }
    except (requests.RequestException, ValueError, TypeError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "payload": payload}


def score_response(case: ScreenCase, result: dict[str, Any], max_tokens: int) -> dict[str, Any]:
    """Return an inspectable, exclusive truncation-first outcome for one trial."""

    if result.get("error"):
        return {"outcome": "error", "categories": ["error"], "details": [result["error"]]}

    text = str(result["text"])
    lowered = text.lower()
    details: list[str] = []
    truncated = result.get("done_reason") == "length" or result.get("eval_count") == max_tokens
    if truncated:
        reason = "done_reason=length" if result.get("done_reason") == "length" else f"eval_count={max_tokens}"
        return {"outcome": "truncated", "categories": ["truncated"], "details": [reason]}

    categories: list[str] = []
    matched_patterns = [pattern for pattern in case.wrong_patterns if re.search(pattern, text, re.IGNORECASE)]
    context_numbers = set(NUMBER_RE.findall(case.context)) | set(case.derived_numbers)
    invalid_numbers = [number for number in NUMBER_RE.findall(text) if number not in context_numbers]
    if matched_patterns or invalid_numbers:
        categories.append("grounded-wrong")
        details.extend([f"contradicted pattern: {pattern}" for pattern in matched_patterns])
        details.extend([f"number absent from context: {number}" for number in invalid_numbers])

    if case.module == "social":
        self_pattern = SELF_COORDINATION_RE_TEMPLATE.format(agent=re.escape(case.agent))
        # "Mara should coordinate with Mara" is covered by the target pattern;
        # duplicate name wording catches variants such as "tell Mara to ask Mara".
        own_name_count = len(re.findall(rf"\b{re.escape(case.agent)}\b", text, re.IGNORECASE))
        if re.search(self_pattern, lowered, re.IGNORECASE) or own_name_count >= 2:
            categories.append("self-coordination")
            details.append(f"coordination target is the agent itself: {case.agent}")

    unknown_names = []
    for match in NAME_RE.finditer(text):
        token = match.group(0)
        if token in case.allowed_names or token in NON_ENTITY_CAPITALS:
            continue
        before = text[:match.start()].rstrip()
        # A normal sentence/quoted-sentence opener ("Hey", "Sounds", etc.)
        # is not a person.  A new agent normally appears as a target after a
        # verb/preposition, which remains checkable below.  The small initial
        # predicate still catches forms such as "Kael should gather wood."
        is_initial_agent_claim = not before and bool(re.match(
            rf"{re.escape(token)}\s+(?:should|is|will|can|needs|has|asks|offers|says|requests)\b",
            text,
            re.IGNORECASE,
        ))
        follows_sentence_boundary = bool(before) and before[-1] in "\"'!? ."
        if is_initial_agent_claim or (before and not follows_sentence_boundary):
            unknown_names.append(token)
    unknown_names = sorted(set(unknown_names))
    if unknown_names:
        categories.append("invented-entity")
        details.append("name absent from context: " + ", ".join(unknown_names))

    if not categories:
        categories = ["clean"]
    return {"outcome": "+".join(categories), "categories": categories, "details": details}


def modal_outcome(trials: list[dict[str, Any]]) -> str:
    """Choose the exact modal outcome; alphabetical tie-breaking is reproducible."""

    counts = Counter(trial["score"]["outcome"] for trial in trials)
    return sorted(counts, key=lambda item: (-counts[item], item))[0]


def print_table(rows: list[list[str]], headers: list[str]) -> None:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    template = "  ".join("{" + f"{index}:<{width}" + "}" for index, width in enumerate(widths))
    print(template.format(*headers))
    print(template.format(*("-" * width for width in widths)))
    for row in rows:
        print(template.format(*row))


def run_screen(args: argparse.Namespace) -> int:
    prompts = load_module_prompts()
    production_max_tokens = load_production_module_max_tokens()
    max_tokens = args.max_tokens if args.max_tokens is not None else production_max_tokens
    selected_cases = CASES
    if args.dry_run:
        print(f"Module quality screen {SCREEN_VERSION}: source and {len(CASES)} fixed cases are valid.")
        print("Modules: " + ", ".join(prompts))
        print("Production payload: " + json.dumps({
            "model": args.model, "max_tokens": max_tokens,
            "temperature": MODULE_TEMPERATURE, **NON_THINKING_SAMPLING, "think": False,
            "timeout_s": args.timeout,
        }, sort_keys=True))
        return 0

    print(f"Module quality screen {SCREEN_VERSION} — {len(selected_cases)} fixed cases × {args.trials} trials")
    print(f"Model={args.model}; endpoint={args.url}; timeout={args.timeout:g}s; "
          f"max_tokens={max_tokens} ({'override' if args.max_tokens is not None else 'production'}); "
          f"temperature={MODULE_TEMPERATURE}; "
          f"top_p=0.8; top_k=20; min_p=0; think=false; concurrent_calls={args.workers}")

    # The live engine runs PIANO work in a two-slot pool.  Retaining that
    # bounded fan-out makes this screen practical without silently testing an
    # unrealistically large request burst.
    task_order = [(case, trial_index) for case in selected_cases for trial_index in range(1, args.trials + 1)]
    with ThreadPoolExecutor(max_workers=args.workers, thread_name_prefix="module-quality") as executor:
        futures = [
            executor.submit(call_ollama, prompts[case.module], case, args.model, args.url, args.timeout, max_tokens)
            for case, _trial_index in task_order
        ]
        trial_results = [future.result() for future in futures]
    results_by_case: dict[str, list[dict[str, Any]]] = {case.case_id: [] for case in selected_cases}
    for (case, trial_index), result in zip(task_order, trial_results):
        results_by_case[case.case_id].append({
            "trial": trial_index, "response": result, "score": score_response(case, result, max_tokens),
        })

    scored_cases: list[dict[str, Any]] = []
    for case in selected_cases:
        trials = results_by_case[case.case_id]
        mode = modal_outcome(trials)
        scored_cases.append({"case": asdict(case), "modal_outcome": mode, "trials": trials})

    rows = []
    for item in scored_cases:
        votes = Counter(trial["score"]["outcome"] for trial in item["trials"])
        vote_text = ", ".join(f"{outcome}={count}" for outcome, count in sorted(votes.items()))
        rows.append([item["case"]["case_id"], item["case"]["module"], item["modal_outcome"], vote_text])
    print()
    print_table(rows, ["case", "module", "modal outcome", "trial outcomes"])

    category_counts = Counter()
    errors = 0
    for item in scored_cases:
        if item["modal_outcome"] == "error":
            errors += 1
            continue
        for category in item["modal_outcome"].split("+"):
            category_counts[category] += 1
    print()
    summary_rows = [[category, f"{category_counts[category]}/{len(selected_cases)}",
                     f"{category_counts[category] / len(selected_cases):.1%}"] for category in CATEGORIES]
    print_table(summary_rows, ["category", "modal cases", "rate"])
    if errors:
        print(f"\nINCOMPLETE: {errors}/{len(selected_cases)} cases had an error as their modal outcome.")

    if args.show_responses:
        print("\nTrial responses and checks:")
        for item in scored_cases:
            print(f"\n{item['case']['case_id']} ({item['modal_outcome']}):")
            for trial in item["trials"]:
                response = trial["response"]
                print(f"  trial {trial['trial']}: {response.get('text') or response.get('error')}")
                for detail in trial["score"]["details"]:
                    print(f"    - {detail}")

    report = {
        "screen_version": SCREEN_VERSION,
        "settings": {
            "model": args.model, "url": args.url, "trials": args.trials, "timeout_s": args.timeout,
            "concurrent_calls": args.workers,
            "max_tokens": max_tokens, "production_max_tokens": production_max_tokens,
            "temperature": MODULE_TEMPERATURE,
            "sampling": NON_THINKING_SAMPLING, "think": False,
        },
        "prompts": prompts,
        "category_counts": {category: category_counts[category] for category in CATEGORIES},
        "case_count": len(selected_cases),
        "modal_errors": errors,
        "cases": scored_cases,
    }
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote {args.json_out}")
    return 1 if errors else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama model id (default: {DEFAULT_MODEL})")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Ollama chat endpoint (default: {DEFAULT_URL})")
    parser.add_argument("--trials", type=int, default=3, help="Trials per fixed case (default: 3)")
    parser.add_argument("--timeout", type=float, default=MODULE_TIMEOUT_S,
                        help=f"Per-request timeout in seconds (default: {MODULE_TIMEOUT_S})")
    parser.add_argument("--workers", type=int, default=PIANO_CONCURRENT_LLM,
                        help=f"Concurrent calls; production PIANO pool size is {PIANO_CONCURRENT_LLM} (default)")
    parser.add_argument("--max-tokens", type=int,
                        help="Override the token budget extracted from server.py for a controlled screen")
    parser.add_argument("--json-out", type=Path, help="Write the full reproducible report to this JSON file")
    parser.add_argument("--show-responses", action="store_true", help="Print each trial text and matched check")
    parser.add_argument("--dry-run", action="store_true", help="Validate source extraction and settings without Ollama")
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("--trials must be at least 1")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    if args.max_tokens is not None and args.max_tokens < 1:
        parser.error("--max-tokens must be positive")
    return args


if __name__ == "__main__":
    raise SystemExit(run_screen(parse_args()))
