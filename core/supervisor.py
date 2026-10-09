"""The Supervisor: runs the screening workflow, one CV at a time (spec 5.7).

Why this file exists:
- It decides the order: CV Analyst → Comparison → Guardrail Reviewer (with at most
  one revise loop) → Scoring → Report Agent → bias filter → save.
- If one CV fails, the error is recorded on that candidate and the run moves on.
- After each candidate it re-ranks everyone and saves the run, so a crash never
  loses finished work.
- Every step sends a TraceEvent to `on_event`, which the UI uses for the live
  timeline and the "behind the scenes" trace.

The Supervisor is plain code. It never makes a hiring decision.
"""

from __future__ import annotations

import time
import uuid
from datetime import date, datetime, timezone
from typing import Callable

from core import agents, guardrails
from core.llm import LLMError
from core.rag import GuidelineIndex
from core.schemas import CandidateResult, IntakeResult, JobRequirements, RunResult, TraceEvent
from core.scoring import score_candidate

EventCallback = Callable[[TraceEvent], None]
SaveCallback = Callable[[RunResult], None]

STAT_NAMES = [
    "candidates", "ai_calls", "cache_hits", "tokens_in", "tokens_out",
    "total_seconds", "repairs", "revisions", "retries", "fallbacks", "errors",
]


def now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def candidate_id_for(intake: IntakeResult) -> str:
    """A short, stable id from the file's content, so the same CV always gets the same id."""
    return intake.content_hash[:12]


def create_run(requirements: JobRequirements, provider: str, model: str) -> RunResult:
    """Start a new, empty screening run."""
    run = RunResult(
        run_id=uuid.uuid4().hex[:12],
        created_at=now_text(),
        provider=provider,
        model=model,
        requirements=requirements,
    )
    run.stats = {name: 0 for name in STAT_NAMES}
    return run


# ===========================================================================
# Trace events and statistics
# ===========================================================================


class Tracer:
    """Collects trace events on the run and passes each one to the UI callback."""

    def __init__(self, run: RunResult, on_event: EventCallback | None) -> None:
        self.run = run
        self.on_event = on_event

    def emit(self, agent: str, action: str, status: str, candidate: str | None = None, **details) -> None:
        event = TraceEvent(ts=now_text(), agent=agent, candidate=candidate, action=action, status=status, **details)
        self.run.trace.append(event)
        if self.on_event is not None:
            self.on_event(event)

    def agent_done(self, agent: str, action: str, candidate: str, step: agents.AgentStep, status: str = "done") -> None:
        """Record a finished AI call, with its tokens, timing and retrieved clauses."""
        meta = step.meta
        add_call_to_stats(self.run, meta)
        self.emit(
            agent,
            action,
            status,
            candidate,
            duration_ms=meta.duration_ms,
            tokens_in=meta.tokens_in,
            tokens_out=meta.tokens_out,
            cache_hit=meta.cache_hit,
            model=meta.model,
            prompt_version=meta.prompt_version,
            retrieved=step.retrieved,
            detail=describe_meta(meta),
        )


def describe_meta(meta) -> str | None:
    """A short note about anything unusual in a call: repairs, retries, fallback."""
    notes = []
    if meta.repairs:
        notes.append(f"{meta.repairs} JSON repair(s)")
    if meta.retries:
        notes.append(f"{meta.retries} retry(ies)")
    if meta.fallback_used:
        notes.append(f"fell back to {meta.model}")
    return ", ".join(notes) if notes else None


def add_call_to_stats(run: RunResult, meta) -> None:
    """Add one AI call's numbers to the run statistics (FR-S6)."""
    for name in STAT_NAMES:
        run.stats.setdefault(name, 0)
    if meta.cache_hit:
        run.stats["cache_hits"] += 1
        return
    run.stats["ai_calls"] += meta.calls
    run.stats["tokens_in"] += meta.tokens_in
    run.stats["tokens_out"] += meta.tokens_out
    run.stats["repairs"] += meta.repairs
    run.stats["retries"] += meta.retries
    if meta.fallback_used:
        run.stats["fallbacks"] += 1


# ===========================================================================
# Screening one candidate
# ===========================================================================


def screen_candidate(
    intake: IntakeResult,
    requirements: JobRequirements,
    llm,
    index: GuidelineIndex | None,
    tracer: Tracer,
    today: date,
) -> CandidateResult:
    """Run the full pipeline for one cleaned CV. Raises on failure (the caller isolates errors)."""
    label = intake.file_name  # replaced by the candidate's name once we know it
    cv_text = intake.clean_text

    # 1. CV Analyst
    tracer.emit("CV Analyst", "Extracting facts from the CV", "started", label)
    profile_step = agents.run_cv_analyst(llm, cv_text, today)
    profile = profile_step.result
    label = profile.name or intake.file_name
    tracer.agent_done("CV Analyst", "Extracted profile", label, profile_step)

    # 2. Comparison Agent
    tracer.emit("Comparison", "Comparing against the requirements", "started", label)
    comparison_step = agents.run_comparison(llm, requirements, profile, cv_text, index)
    assessment = comparison_step.result
    tracer.agent_done("Comparison", "Assessed every requirement", label, comparison_step)

    # 3. Guardrail Reviewer: quote check, with one revise loop at most
    failing_ids = agents.check_quotes(assessment, cv_text)
    revised = False
    if failing_ids:
        tracer.emit(
            "Guardrail Reviewer",
            "Quotes not found in the CV; sending back to Comparison once",
            "warning",
            label,
            detail=", ".join(failing_ids),
        )
        revision_step = agents.run_comparison_revision(
            llm, requirements, profile, cv_text, index, assessment, failing_ids
        )
        assessment = revision_step.result
        revised = True
        tracer.run.stats["revisions"] = tracer.run.stats.get("revisions", 0) + 1
        tracer.agent_done("Comparison", "Revised the unverified quotes", label, revision_step, status="revised")
        agents.check_quotes(assessment, cv_text)

    review = agents.finish_review(assessment, requirements, profile, intake)
    review_note = f"{len(review.flags)} flag(s)"
    if review.downgraded_ids:
        review_note += f"; still unverified, marked missing: {', '.join(review.downgraded_ids)}"
    tracer.emit("Guardrail Reviewer", "Checked quotes, years and flags", "done", label, detail=review_note)

    # 4. Scoring (code)
    score = score_candidate(requirements, review.assessment.results)
    tracer.emit("Scoring", "Calculated the score", "done", label, detail=f"{score.overall} ({score.band})")

    # 5. Report Agent, then the bias filter
    tracer.emit("Report", "Writing the candidate brief", "started", label)
    report_step, removed = agents.run_candidate_report(
        llm, profile, review.assessment, requirements, score, review.missing_info, review.flags, index
    )
    flags = review.flags + guardrails.bias_filtered_flag(removed)
    tracer.agent_done("Report", "Wrote the candidate brief", label, report_step)
    if removed:
        tracer.emit("Bias filter", "Removed text mentioning protected characteristics", "warning", label,
                    detail=f"{len(removed)} item(s) removed")

    return CandidateResult(
        candidate_id=candidate_id_for(intake),
        file_name=intake.file_name,
        profile=profile,
        assessment=review.assessment,
        report=report_step.result,
        score=score,
        missing_info=review.missing_info,
        flags=flags,
        redactions=intake.redactions,
        quarantined_text=intake.quarantined_text,
        revised=revised,
        needs_attention=review.needs_attention,
    )


def friendly_error(error: Exception) -> str:
    """A message safe to show the recruiter, never a raw Python error."""
    if isinstance(error, (LLMError, agents.AgentError)):
        return str(error)
    return f"Something went wrong while screening this CV ({type(error).__name__}). Click Retry to try again."


def failed_candidate(intake: IntakeResult, message: str) -> CandidateResult:
    """A result that records the failure, keeping the intake details."""
    return CandidateResult(
        candidate_id=candidate_id_for(intake),
        file_name=intake.file_name,
        flags=guardrails.intake_flags(intake),
        redactions=intake.redactions,
        quarantined_text=intake.quarantined_text,
        error=message,
    )


# ===========================================================================
# Ranking and the run loop
# ===========================================================================


def candidate_name(candidate: CandidateResult) -> str:
    if candidate.profile is not None:
        return candidate.profile.name
    return candidate.file_name


def rank_key(candidate: CandidateResult) -> tuple:
    """Sort order: errors last; then score (highest first), fewer must-have gaps, name."""
    has_error = candidate.error is not None or candidate.score is None
    score = candidate.score.overall if candidate.score else 0.0
    gaps = len(candidate.score.must_have_gaps) if candidate.score else 0
    return (has_error, -score, gaps, candidate_name(candidate).lower())


def rank_candidates(run: RunResult) -> None:
    run.candidates.sort(key=rank_key)


def find_candidate(run: RunResult, candidate_id: str) -> CandidateResult | None:
    for candidate in run.candidates:
        if candidate.candidate_id == candidate_id:
            return candidate
    return None


def store_result(run: RunResult, result: CandidateResult) -> None:
    """Replace an earlier result for the same candidate, or add a new one."""
    for position, candidate in enumerate(run.candidates):
        if candidate.candidate_id == result.candidate_id:
            run.candidates[position] = result
            return
    run.candidates.append(result)


def screen_one_safely(
    intake: IntakeResult, run: RunResult, llm, index: GuidelineIndex | None, tracer: Tracer, today: date
) -> CandidateResult:
    """Screen one CV; if anything fails, return an error result instead of stopping the run."""
    started = time.perf_counter()
    try:
        result = screen_candidate(intake, run.requirements, llm, index, tracer, today)
    except Exception as error:  # isolate every failure to this one candidate (FR-S3)
        message = friendly_error(error)
        tracer.emit("Supervisor", "Screening failed for this CV", "error", intake.file_name, detail=message)
        result = failed_candidate(intake, message)
        run.stats["errors"] = run.stats.get("errors", 0) + 1
    run.stats["total_seconds"] = round(run.stats.get("total_seconds", 0) + time.perf_counter() - started, 1)
    return result


def needs_screening(run: RunResult, intake: IntakeResult, only_new: bool) -> bool:
    existing = find_candidate(run, candidate_id_for(intake))
    if existing is None:
        return True
    if only_new:
        return False
    return existing.error is not None  # a failed CV is tried again by Run screening


def run_screening(
    run: RunResult,
    documents: list[IntakeResult],
    llm,
    index: GuidelineIndex | None,
    on_event: EventCallback | None = None,
    save_run: SaveCallback | None = None,
    today: date | None = None,
    only_new: bool = False,
) -> RunResult:
    """Screen every CV that hasn't been screened yet (FR-S1), one at a time.

    After each candidate: re-rank, update the statistics, save, and emit an event.
    """
    today = today or date.today()
    tracer = Tracer(run, on_event)
    to_screen = [intake for intake in documents if needs_screening(run, intake, only_new)]
    tracer.emit("Supervisor", f"Screening {len(to_screen)} CV(s)", "started")

    for position, intake in enumerate(to_screen, start=1):
        result = screen_one_safely(intake, run, llm, index, tracer, today)
        store_result(run, result)
        rank_candidates(run)
        run.stats["candidates"] = len(run.candidates)
        if save_run is not None:
            save_run(run)  # saved after every candidate (FR-S4)
        status = "error" if result.error else "done"
        tracer.emit("Supervisor", f"Finished {position} of {len(to_screen)} and saved", status, candidate_name(result))

    tracer.emit("Supervisor", "Screening complete", "done", detail=f"{len(run.candidates)} candidate(s) ranked")
    return run


def screen_new_cvs(
    run: RunResult,
    documents: list[IntakeResult],
    llm,
    index: GuidelineIndex | None,
    on_event: EventCallback | None = None,
    save_run: SaveCallback | None = None,
    today: date | None = None,
) -> RunResult:
    """Screen only CVs added since the run started, adding them to the same ranking (FR-S5)."""
    return run_screening(run, documents, llm, index, on_event, save_run, today, only_new=True)


def retry_candidate(
    run: RunResult,
    intake: IntakeResult,
    llm,
    index: GuidelineIndex | None,
    on_event: EventCallback | None = None,
    save_run: SaveCallback | None = None,
    today: date | None = None,
) -> CandidateResult:
    """Screen one CV again (the Retry button on an error row)."""
    tracer = Tracer(run, on_event)
    result = screen_one_safely(intake, run, llm, index, tracer, today or date.today())
    store_result(run, result)
    rank_candidates(run)
    run.stats["candidates"] = len(run.candidates)
    if save_run is not None:
        save_run(run)
    return result
