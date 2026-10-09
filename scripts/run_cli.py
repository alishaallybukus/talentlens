"""Runs the whole TalentLens screening pipeline from the command line, without the app.

Run it with:  python scripts/run_cli.py
Options:      --provider ollama   --model gemini-3.7-flash   --no-cache   --limit 2

What it does:
1. Reads the sample job description and hiring guidelines.
2. Runs the Job Analyst to build the requirements checklist.
3. Reads the 6 sample CVs, applies the intake guardrails, and screens them.
4. Prints a ranking table with bands, missing information and flags.

Why: Phase 2, Gate A. The pipeline must work here before any UI is built.
The run is saved to the database, and AI answers are cached, so a second run is fast.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Let this script import from `core/` even though it lives in `scripts/`.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.agents import AgentError, run_job_analyst  # noqa: E402
from core.config import get_settings  # noqa: E402
from core.documents import DocumentReadError, content_hash, read_document  # noqa: E402
from core.guardrails import run_intake  # noqa: E402
from core.llm import LLMClient, LLMError  # noqa: E402
from core.memory import Memory  # noqa: E402
from core.rag import build_index  # noqa: E402
from core.schemas import IntakeResult, RunResult, TraceEvent  # noqa: E402
from core.supervisor import create_run, run_screening  # noqa: E402

SAMPLE_DIR = PROJECT_ROOT / "data" / "sample"


def read_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the TalentLens pipeline on the sample CVs.")
    parser.add_argument("--provider", choices=["gemini", "ollama"], help="AI provider (default: from .env)")
    parser.add_argument("--model", help="Model name (default: from .env)")
    parser.add_argument("--no-cache", action="store_true", help="Ignore saved AI answers")
    parser.add_argument("--limit", type=int, help="Only screen the first N CVs")
    return parser.parse_args()


def load_sample_cvs(limit: int | None) -> list[IntakeResult]:
    """Read every sample CV and run the intake guardrails on it."""
    documents: list[IntakeResult] = []
    for path in sorted((SAMPLE_DIR / "cvs").iterdir()):
        data = path.read_bytes()
        try:
            text = read_document(path.name, data)
        except DocumentReadError as error:
            print(f"  Skipped {path.name}: {error}")
            continue
        documents.append(run_intake(path.name, text, content_hash(data)))
    if limit:
        documents = documents[:limit]
    return documents


def print_event(event: TraceEvent) -> None:
    """Print a short line for each finished step, so you can watch the run."""
    if event.status == "started":
        return
    who = f" {event.candidate}" if event.candidate else ""
    timing = ""
    if event.duration_ms:
        source = "cache" if event.cache_hit else f"{event.tokens_in}+{event.tokens_out} tokens"
        timing = f" ({event.duration_ms / 1000:.1f}s, {source})"
    detail = f" - {event.detail}" if event.detail else ""
    print(f"  [{event.agent}]{who}: {event.action}{timing}{detail}")


def short(items: list[str], empty: str = "-", width: int = 0) -> str:
    """Join a list for the table, cutting it off with "..." if it's wider than `width`."""
    text = ", ".join(items) if items else empty
    if width and len(text) > width - 2:
        text = text[: width - 5] + "..."
    return text


def print_ranking(run: RunResult) -> None:
    """The ranking table: one row per candidate, best first."""
    print("\nRANKING (AI recommendation, not a decision)\n")
    header = f"{'#':<3}{'Candidate':<20}{'Score':>6}  {'Band':<26}{'Must-have gaps':<30}{'Missing info':<45}Flags"
    print(header)
    print("-" * len(header))
    for position, candidate in enumerate(run.candidates, start=1):
        name = candidate.profile.name if candidate.profile else candidate.file_name
        if candidate.error:
            print(f"{position:<3}{name:<20}{'':>6}  ERROR: {candidate.error}")
            continue
        flags = [flag.code for flag in candidate.flags]
        if candidate.revised:
            flags.append("(revised)")
        print(
            f"{position:<3}{name:<20}{candidate.score.overall:>6.1f}  {candidate.score.band:<26}"
            f"{short(candidate.score.must_have_gaps, width=30):<30}"
            f"{short(candidate.missing_info, width=45):<45}{short(flags)}"
        )


def print_statistics(run: RunResult, seconds: float) -> None:
    stats = run.stats
    print(
        f"\nAI calls: {stats.get('ai_calls', 0)}, cache hits: {stats.get('cache_hits', 0)}, "
        f"tokens in/out: {stats.get('tokens_in', 0)}/{stats.get('tokens_out', 0)}, "
        f"repairs: {stats.get('repairs', 0)}, revisions: {stats.get('revisions', 0)}, "
        f"retries: {stats.get('retries', 0)}, fallbacks: {stats.get('fallbacks', 0)}, "
        f"total time: {seconds:.0f}s"
    )


def main() -> int:
    # Make sure symbols such as "§" print correctly in any Windows terminal,
    # and that each line appears straight away (even when the output is redirected).
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    arguments = read_arguments()
    started = time.perf_counter()

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
    print(f"TalentLens command-line run: {llm.provider} / {llm.model} (cache {'on' if llm.use_cache else 'off'})\n")

    job_description = (SAMPLE_DIR / "job_description.md").read_text(encoding="utf-8")
    index = build_index((SAMPLE_DIR / "hiring_guidelines.md").read_text(encoding="utf-8"))
    preferences = [row["text"] for row in memory.list_preferences()]

    try:
        print("1. Job Analyst: building the requirements checklist...")
        job_step = run_job_analyst(llm, job_description, preferences, index)
        requirements = job_step.result
        cached = " (from cache)" if job_step.meta.cache_hit else ""
        band = "not stated"
        if requirements.salary_min and requirements.salary_max:
            band = f"{requirements.salary_min:,.0f} to {requirements.salary_max:,.0f} {requirements.currency or ''}"
        print(f"   {len(requirements.must_have)} must-haves, {len(requirements.nice_to_have)} nice-to-haves, "
              f"salary band {band}{cached}")
        for requirement in requirements.must_have + requirements.nice_to_have:
            years = f" (min {requirement.min_years:g} years)" if requirement.min_years else ""
            print(f"   {requirement.id}: {requirement.label}{years}")

        documents = load_sample_cvs(arguments.limit)
        print(f"\n2. Screening {len(documents)} CVs...")
        run = create_run(requirements, llm.provider, llm.model)
        for intake in documents:  # load them into the app's working area too, like "Use sample CVs"
            memory.add_document(intake, kind="cv")
        run_screening(run, documents, llm, index, on_event=print_event, save_run=memory.save_run)
    except (LLMError, AgentError) as error:
        print(f"\nThe run stopped: {error}")
        return 1
    finally:
        memory.close()

    print_ranking(run)
    print_statistics(run, time.perf_counter() - started)
    print(f"\nSaved as run {run.run_id}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
