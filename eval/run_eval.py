"""Evaluation: run the real pipeline on all 7 fictional CVs and compare the results with ground_truth.json.

Run it with:  python -m eval.run_eval --provider ollama   (or --provider gemini)

Why this file exists (spec 15.2):
- Unit tests use a fake model. This checks that a REAL model gives the right
  answers on the demo CVs: the band, the missing information and the flags.
- For each CV it checks:
  - the band is one of the acceptable bands
  - the missing information matches exactly
  - every expected flag is there, and there's no false "prompt injection" flag
- It also measures how many quotes were verified on the first try, revisions,
  JSON repairs, time and tokens per CV.
- Results go to eval/results/eval_{provider}_{timestamp}.json (shown on the
  Behind the scenes page) and a Markdown table next to it.
- The evaluation run is NOT saved as the app's current run. It does share the
  answer cache, so CVs already screened with the same prompts come back instantly.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from core.agents import AgentError, run_job_analyst
from core.config import PROJECT_ROOT, get_settings
from core.documents import content_hash, read_document_file
from core.guardrails import run_intake
from core.llm import LLMClient, LLMError
from core.memory import Memory
from core.rag import build_index
from core.schemas import CandidateResult, IntakeResult, JobRequirements, RunResult
from core.supervisor import create_run, run_screening

SAMPLE_DIR: Path = PROJECT_ROOT / "data" / "sample"
GROUND_TRUTH_FILE = SAMPLE_DIR / "ground_truth.json"
APPROVED_REQUIREMENTS_FILE = SAMPLE_DIR / "approved_requirements.json"
RESULTS_DIR: Path = PROJECT_ROOT / "eval" / "results"
SAMPLE_JOB_TITLE = "Marketing Executive"

# The CVs in evaluation order: the 6 sample CVs, then Nadia (the live-demo CV).
CV_FOLDERS: list[Path] = [SAMPLE_DIR / "cvs", SAMPLE_DIR / "live_demo"]


# ===========================================================================
# Inputs
# ===========================================================================


def load_ground_truth() -> dict[str, dict]:
    return json.loads(GROUND_TRUTH_FILE.read_text(encoding="utf-8"))


def load_cvs(limit: int | None) -> list[IntakeResult]:
    """Read and clean every CV, exactly as the app's intake does."""
    documents = []
    for folder in CV_FOLDERS:
        for path in sorted(folder.iterdir()):
            text = read_document_file(path)
            documents.append(run_intake(path.name, text, content_hash(path.read_bytes())))
    return documents[:limit] if limit else documents


def load_requirements(memory: Memory, llm, index) -> tuple[JobRequirements, str]:
    """The requirements to screen against, and where they came from.

    1. the requirements the recruiter approved in the app for the sample job
    2. data/sample/approved_requirements.json, if it exists
    3. otherwise the Job Analyst's extraction, unedited
    """
    job = memory.get_job_by_title(SAMPLE_JOB_TITLE)
    if job is not None:
        return job["requirements"], f"approved in the app by {job['approved_by']}"
    if APPROVED_REQUIREMENTS_FILE.exists():
        text = APPROVED_REQUIREMENTS_FILE.read_text(encoding="utf-8")
        return JobRequirements.model_validate_json(text), APPROVED_REQUIREMENTS_FILE.name
    job_description = (SAMPLE_DIR / "job_description.md").read_text(encoding="utf-8")
    preferences = [row["text"] for row in memory.list_preferences()]
    step = run_job_analyst(llm, job_description, preferences, index)
    return step.result, "extracted by the Job Analyst (not edited)"


# ===========================================================================
# Comparing one CV with the ground truth
# ===========================================================================


def check_band(candidate: CandidateResult, expected: dict) -> bool:
    return candidate.score is not None and candidate.score.band in expected["acceptable_tiers"]


def check_missing_info(candidate: CandidateResult, expected: dict) -> bool:
    return sorted(candidate.missing_info) == sorted(expected["missing_info"])


def check_flags(candidate: CandidateResult, expected: dict) -> tuple[bool, list[str]]:
    """Every expected flag present, and no prompt_injection unless expected. Returns (passed, problems)."""
    codes = {flag.code for flag in candidate.flags}
    problems = [f"missing flag {code}" for code in expected["flags"] if code not in codes]
    if "prompt_injection" in codes and "prompt_injection" not in expected["flags"]:
        problems.append("false prompt_injection flag")
    return not problems, problems


def trace_for(run: RunResult, candidate: CandidateResult) -> list:
    """The trace events for one candidate (the first events use the file name, later ones the real name)."""
    names = {candidate.file_name}
    if candidate.profile:
        names.add(candidate.profile.name)
    return [event for event in run.trace if event.candidate in names]


def quote_counts(run: RunResult, candidate: CandidateResult) -> tuple[int, int, int]:
    """(quotes checked, failed on the first try, verified at the end).

    "Quotes checked" are met or partial answers, including ones later marked missing
    because their quote was never found. First-try failures come from the
    Guardrail Reviewer's warning, which lists the requirement ids it sent back.
    """
    if candidate.assessment is None:
        return 0, 0, 0
    checked = 0
    verified = 0
    for result in candidate.assessment.results:
        was_claimed = result.status in ("met", "partial") or result.original_status in ("met", "partial")
        if was_claimed:
            checked += 1
        if result.status in ("met", "partial") and result.verified:
            verified += 1
    first_try_failures = 0
    for event in trace_for(run, candidate):
        if event.agent == "Guardrail Reviewer" and event.status == "warning" and event.detail:
            first_try_failures += len([part for part in event.detail.split(",") if part.strip()])
    return checked, first_try_failures, verified


def evaluate_candidate(run: RunResult, candidate: CandidateResult, expected: dict) -> dict:
    """One row of the results table."""
    events = trace_for(run, candidate)
    seconds = sum(event.duration_ms for event in events) / 1000
    tokens = sum(event.tokens_in + event.tokens_out for event in events)
    row = {
        "cv": candidate.file_name,
        "name": candidate.profile.name if candidate.profile else "",
        "band": candidate.score.band if candidate.score else "",
        "score": candidate.score.overall if candidate.score else None,
        "expected_bands": " or ".join(expected["acceptable_tiers"]),
        "missing_info": ", ".join(candidate.missing_info),
        "expected_missing": ", ".join(expected["missing_info"]),
        "flags": ", ".join(flag.code for flag in candidate.flags),
        "seconds": round(seconds, 1),
        "tokens": tokens,
        "revised": candidate.revised,
        "problems": "",
        "passed": False,
    }
    if candidate.error:
        row["problems"] = f"error: {candidate.error}"
        return row

    problems = []
    if not check_band(candidate, expected):
        problems.append(f"band {row['band']}")
    if not check_missing_info(candidate, expected):
        problems.append("missing info differs")
    flags_ok, flag_problems = check_flags(candidate, expected)
    problems += flag_problems
    checked, first_failures, verified = quote_counts(run, candidate)
    row["quotes_checked"] = checked
    row["quotes_failed_first_try"] = first_failures
    row["quotes_verified"] = verified
    row["problems"] = "; ".join(problems)
    row["passed"] = not problems
    return row


# ===========================================================================
# Summary across all CVs
# ===========================================================================


def missing_info_precision_recall(candidates: list[CandidateResult], truth: dict[str, dict]) -> tuple[float, float]:
    """Precision: of what we flagged as missing, how much really was. Recall: of what was missing, how much we found."""
    true_positives = 0
    predicted = 0
    actual = 0
    for candidate in candidates:
        found = set(candidate.missing_info)
        expected = set(truth[candidate.file_name]["missing_info"])
        true_positives += len(found & expected)
        predicted += len(found)
        actual += len(expected)
    precision = true_positives / predicted if predicted else 1.0
    recall = true_positives / actual if actual else 1.0
    return round(precision * 100, 1), round(recall * 100, 1)


def percent(part: int, whole: int) -> float:
    return round(part / whole * 100, 1) if whole else 100.0


def summarise(run: RunResult, rows: list[dict], truth: dict[str, dict]) -> dict:
    screened = [candidate for candidate in run.candidates if candidate.error is None]
    precision, recall = missing_info_precision_recall(screened, truth)
    checked = sum(row.get("quotes_checked", 0) for row in rows)
    first_failures = sum(row.get("quotes_failed_first_try", 0) for row in rows)
    count = len(rows) or 1
    return {
        "passed": f"{sum(row['passed'] for row in rows)} of {len(rows)}",
        "band_accuracy_pct": percent(sum(1 for c in screened if check_band(c, truth[c.file_name])), len(rows)),
        "missing_info_precision_pct": precision,
        "missing_info_recall_pct": recall,
        "quotes_verified_first_try_pct": percent(checked - first_failures, checked),
        "revisions": run.stats.get("revisions", 0),
        "json_repairs": run.stats.get("repairs", 0),
        "avg_seconds_per_cv": round(sum(row["seconds"] for row in rows) / count, 1),
        "avg_tokens_per_cv": round(sum(row["tokens"] for row in rows) / count),
        "cache_hits": run.stats.get("cache_hits", 0),
        "errors": sum(1 for row in rows if row["problems"].startswith("error")),
    }


# ===========================================================================
# Output
# ===========================================================================


def markdown_table(result: dict) -> str:
    lines = [
        f"## Evaluation: {result['provider']} / {result['model']} ({result['timestamp']})",
        "",
        f"Requirements: {result['requirements_source']}",
        "",
        "| CV | Band | Expected | Missing info | Flags | Time (s) | Tokens | Result |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in result["cases"]:
        verdict = "✅ pass" if row["passed"] else f"❌ {row['problems']}"
        lines.append(
            f"| {row['name'] or row['cv']} | {row['band']} | {row['expected_bands']} | {row['missing_info'] or '-'} "
            f"| {row['flags'] or '-'} | {row['seconds']} | {row['tokens']} | {verdict} |"
        )
    lines += ["", "| Metric | Value |", "|---|---|"]
    lines += [f"| {name.replace('_', ' ')} | {value} |" for name, value in result["summary"].items()]
    return "\n".join(lines) + "\n"


def save_results(result: dict) -> tuple[Path, Path]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"eval_{result['provider']}_{result['timestamp']}"
    json_path = RESULTS_DIR / f"{stem}.json"
    markdown_path = RESULTS_DIR / f"{stem}.md"
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    markdown_path.write_text(markdown_table(result), encoding="utf-8")
    return json_path, markdown_path


# ===========================================================================
# Main
# ===========================================================================


def read_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate TalentLens against ground_truth.json.")
    parser.add_argument("--provider", choices=["gemini", "ollama"], help="AI provider (default: from .env)")
    parser.add_argument("--model", help="Model name (default: from .env)")
    parser.add_argument("--limit", type=int, help="Only evaluate the first N CVs")
    parser.add_argument("--no-cache", action="store_true", help="Ignore saved AI answers")
    return parser.parse_args(argv)


def print_progress(event) -> None:
    if event.agent == "Supervisor" and event.action.startswith("Finished"):
        action = event.action.replace(" and saved", "")  # evaluation runs aren't saved as the app's run
        print(f"  {action}: {event.candidate}", flush=True)


def evaluate(run: RunResult, truth: dict[str, dict]) -> list[dict]:
    return [evaluate_candidate(run, candidate, truth[candidate.file_name]) for candidate in run.candidates]


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    arguments = read_arguments(argv)
    settings = get_settings()
    memory = Memory(settings.database_url)
    overrides: dict = {}
    if arguments.provider:
        overrides["provider"] = arguments.provider
    if arguments.model:
        overrides["model"] = arguments.model
    if arguments.no_cache:
        overrides["use_cache"] = False
    llm = LLMClient.from_settings(settings, memory, **overrides)
    index = build_index((SAMPLE_DIR / "hiring_guidelines.md").read_text(encoding="utf-8"))
    truth = load_ground_truth()
    print(f"Evaluating {llm.provider} / {llm.model} (cache {'on' if llm.use_cache else 'off'})")

    started = time.perf_counter()
    try:
        requirements, source = load_requirements(memory, llm, index)
        print(f"Requirements: {source}")
        documents = load_cvs(arguments.limit)
        run = create_run(requirements, llm.provider, llm.model)
        run_screening(run, documents, llm, index, on_event=print_progress)  # not saved as the app's run
    except (LLMError, AgentError) as error:
        print(f"The evaluation stopped: {error}")
        return 1
    finally:
        memory.close()

    rows = evaluate(run, truth)
    result = {
        "provider": llm.provider,
        "model": llm.model,
        "timestamp": datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
        "requirements_source": source,
        "total_seconds": round(time.perf_counter() - started),
        "summary": summarise(run, rows, truth),
        "cases": rows,
    }
    json_path, markdown_path = save_results(result)
    print()
    print(markdown_table(result))
    print(f"Saved {json_path.relative_to(PROJECT_ROOT)} and {markdown_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
