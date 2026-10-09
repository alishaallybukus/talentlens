"""The agents: Job Analyst, CV Analyst, Comparison, Guardrail Reviewer and Report (spec section 5).

Why this file exists:
- Each AI agent is one function: build its prompt (from core/prompts.py), call the
  LLM layer, then apply the code checks that keep the answer honest.
- The Guardrail Reviewer is plain code (no AI). It checks quotes against the CV,
  caps "met" when experience is below the minimum years, removes career-gap
  concerns, and raises flags.
- Agents only ever see CLEANED CV text (redacted and quarantined at intake).

The `llm` argument is anything with a generate_json(...) method: the real
LLMClient, or the FakeLLM used in tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from core import guardrails, prompts
from core.rag import GuidelineIndex, clause_ids, format_clauses_for_prompt
from core.schemas import (
    CandidateProfile,
    CandidateReport,
    Flag,
    IntakeResult,
    InterviewQuestion,
    JobRequirements,
    MatchAssessment,
    Requirement,
    RequirementResult,
    ScoreBreakdown,
    ShortlistOverview,
)

# Fixed retrieval queries for each agent (spec section 5).
JOB_ANALYST_QUERY = "requirements minimum years experience salary band scoring rubric"
COMPARISON_QUERY = "evidence quote met partial missing transferable experience minimum years scoring"
REPORT_QUERY = "interview questions behavioural protected characteristics gaps clarification"

MAX_KEY_SKILLS = 6
MIN_QUESTIONS = 3
MAX_QUESTIONS = 5


class AgentError(Exception):
    """An agent's answer can't be used. The message is written for the recruiter."""


@dataclass
class AgentStep:
    """What one agent produced: the result, the call metadata and the clauses it was given."""

    result: Any
    meta: Any = None  # CallMeta from core/llm.py (None for code-only steps)
    retrieved: list[str] = field(default_factory=list)


def retrieve(index: GuidelineIndex | None, query: str) -> tuple[str, list[str]]:
    """Find the top guideline clauses. Returns (text for the prompt, clause ids)."""
    if index is None:
        return "(no guidelines loaded)", []
    clauses = index.search(query)
    return format_clauses_for_prompt(clauses), clause_ids(clauses)


# ===========================================================================
# Job Analyst Agent (spec 5.2)
# ===========================================================================


def renumber_if_needed(requirements: list[Requirement], prefix: str) -> None:
    """Make ids unique and correctly prefixed (M1, M2... or N1, N2...).

    If the model's ids are already fine they are kept, so preference notes still match.
    """
    ids = [requirement.id for requirement in requirements]
    ids_look_right = all(re.fullmatch(rf"{prefix}\d+", requirement_id) for requirement_id in ids)
    if ids_look_right and len(set(ids)) == len(ids):
        return
    for number, requirement in enumerate(requirements, start=1):
        requirement.id = f"{prefix}{number}"


def check_job_requirements(requirements: JobRequirements) -> JobRequirements:
    """Code checks after the Job Analyst: unique ids and at least one must-have."""
    if not requirements.must_have:
        raise AgentError("The Job Analyst found no must-have requirements. Check the job description.")
    renumber_if_needed(requirements.must_have, "M")
    renumber_if_needed(requirements.nice_to_have, "N")
    return requirements


def run_job_analyst(
    llm, job_description: str, preferences: list[str], index: GuidelineIndex | None
) -> AgentStep:
    """Turn the job description into a requirements checklist."""
    template = prompts.JOB_ANALYST
    guideline_text, retrieved = retrieve(index, JOB_ANALYST_QUERY)
    preference_text = "\n".join(f"- {text}" for text in preferences) if preferences else "(none)"
    prompt = prompts.fill(
        template.user,
        {
            "guidelines": guideline_text,
            "preferences": prompts.neutralise_tags(preference_text),
            "job_description": prompts.neutralise_tags(job_description),
            "schema": prompts.schema_text(JobRequirements),
        },
    )
    requirements, meta = llm.generate_json(
        "job_analyst", template.system, prompt, JobRequirements, template.temperature, template.version
    )
    return AgentStep(check_job_requirements(requirements), meta, retrieved)


# ===========================================================================
# CV Analyst Agent (spec 5.3)
# ===========================================================================

EMAIL_PATTERN = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
PHONE_PATTERN = r"\+\d{1,3}(?:[\s-]?\d{2,4}){2,4}"

# Labelled lines such as "Availability: 2 weeks", for the code fallback below.
# Each profile field lists the labels a CV may use for it.
LABELLED_FIELDS: dict[str, list[str]] = {
    "salary_expectation": ["Salary expectation", "Expected salary"],
    "notice_period": ["Notice period", "Availability"],
    "right_to_work": ["Right to work", "Work permit"],
    "references": ["References"],
}


def labelled_value(cv_text: str, labels: list[str]) -> str | None:
    """The text after "Label:" at the start of a CV line, or None."""
    for label in labels:
        found = re.search(rf"^\s*{re.escape(label)}\s*:\s*(.+?)\s*$", cv_text, flags=re.MULTILINE | re.IGNORECASE)
        if found:
            return found.group(1)
    return None


def fill_labelled_fields(profile: CandidateProfile, cv_text: str) -> None:
    """Fill details the model left empty when the CV states them on a labelled line.

    Seen with qwen2.5: "Availability: Immediately" was not read as a notice period,
    so the candidate was wrongly flagged as missing it.
    """
    for field_name, labels in LABELLED_FIELDS.items():
        if guardrails.is_missing(getattr(profile, field_name)):
            value = labelled_value(cv_text, labels)
            if value:
                setattr(profile, field_name, value)


def restore_present_end_dates(profile: CandidateProfile, cv_text: str) -> None:
    """A role with a start but no end is set to "Present" when the CV says "<start> - Present".

    Seen with qwen2.5: the end "Present" was dropped, so the years couldn't be recalculated.
    """
    for role in profile.roles:
        if role.start and not role.end:
            pattern = rf"{re.escape(role.start)}\s*[-–—]\s*(present|current|now|today)\b"
            if re.search(pattern, cv_text, flags=re.IGNORECASE):
                role.end = "Present"


def fix_profile(profile: CandidateProfile, cv_text: str, today: date) -> CandidateProfile:
    """Code fixes after the CV Analyst.

    - Fill in email and phone with a simple pattern search if the model missed them.
    - Fill in salary, notice period, right to work and references from labelled lines.
    - Put back a dropped "Present" end date.
    - Recalculate years from the role dates (overlaps merged, career breaks excluded).
      If the dates can't be read, the model's numbers are kept.
    - Recalculate career gaps from the dates too.
    """
    if guardrails.is_missing(profile.email):
        found = re.search(EMAIL_PATTERN, cv_text)
        profile.email = found.group(0) if found else None
    if guardrails.is_missing(profile.phone):
        found = re.search(PHONE_PATTERN, cv_text)
        profile.phone = found.group(0).strip() if found else None
    fill_labelled_fields(profile, cv_text)
    restore_present_end_dates(profile, cv_text)

    # A model can invent a "career break" role (seen with qwen2.5 on Kevin's CV).
    # A break is only kept if the CV itself mentions one.
    cv_mentions_break = re.search(guardrails.CAREER_BREAK_PATTERN, cv_text, flags=re.IGNORECASE) is not None
    if not cv_mentions_break:
        profile.roles = [role for role in profile.roles if not guardrails.is_career_break(role)]

    years = guardrails.compute_experience_years(profile.roles, today)
    if years is not None:
        profile.total_years_experience = years.total
        profile.relevant_years_experience = years.relevant
        profile.career_gaps = guardrails.find_career_gaps(profile.roles, today)
    elif not cv_mentions_break:
        # The dates can't be checked, so the model's gaps can't be either: drop them
        # rather than ask a candidate about a gap that may not exist.
        profile.career_gaps = []
    return profile


def run_cv_analyst(llm, cv_text: str, today: date) -> AgentStep:
    """Extract the facts from one cleaned CV."""
    template = prompts.CV_ANALYST
    prompt = prompts.fill(
        template.user,
        {"cv": prompts.neutralise_tags(cv_text), "schema": prompts.schema_text(CandidateProfile)},
    )
    profile, meta = llm.generate_json(
        "cv_analyst", template.system, prompt, CandidateProfile, template.temperature, template.version
    )
    return AgentStep(fix_profile(profile, cv_text, today), meta, [])


# ===========================================================================
# Comparison Agent (spec 5.4)
# ===========================================================================


def all_requirements(requirements: JobRequirements) -> list[Requirement]:
    return requirements.must_have + requirements.nice_to_have


def requirements_for_prompt(requirements: JobRequirements) -> str:
    """The checklist as JSON, with each requirement's type spelled out."""
    rows = []
    for requirement_type, group in (("must-have", requirements.must_have), ("nice-to-have", requirements.nice_to_have)):
        for requirement in group:
            rows.append(
                {
                    "id": requirement.id,
                    "type": requirement_type,
                    "label": requirement.label,
                    "description": requirement.description,
                    "keywords": requirement.keywords,
                    "min_years": requirement.min_years,
                }
            )
    return prompts.as_json(rows)


def computed_years_text(profile: CandidateProfile) -> str:
    return (
        f"Relevant (marketing) experience, computed from the role dates: "
        f"{profile.relevant_years_experience} years. Total experience: {profile.total_years_experience} years."
    )


def align_results(assessment: MatchAssessment, requirements: JobRequirements) -> MatchAssessment:
    """Make sure there is exactly one result per requirement, in checklist order.

    Unknown ids are dropped; a requirement the model skipped is marked missing.
    """
    first_result_by_id: dict[str, RequirementResult] = {}
    for result in assessment.results:
        if result.requirement_id not in first_result_by_id:
            first_result_by_id[result.requirement_id] = result

    aligned: list[RequirementResult] = []
    for requirement in all_requirements(requirements):
        result = first_result_by_id.get(requirement.id)
        if result is None:
            result = RequirementResult(
                requirement_id=requirement.id,
                status="missing",
                evidence=None,
                reasoning="The AI returned no assessment for this requirement, so it counts as missing (§3.2).",
                guideline_refs=["§3.2"],
            )
        aligned.append(result)
    assessment.results = aligned
    return assessment


def build_comparison_prompt(
    requirements: JobRequirements, profile: CandidateProfile, cv_text: str, guideline_text: str
) -> str:
    profile_data = profile.model_dump(exclude={"email", "phone"})  # contact details aren't needed here
    return prompts.fill(
        prompts.COMPARISON.user,
        {
            "requirements": requirements_for_prompt(requirements),
            "guidelines": guideline_text,
            "profile": prompts.neutralise_tags(prompts.as_json(profile_data)),
            "computed_years": computed_years_text(profile),
            "cv": prompts.neutralise_tags(cv_text),
            "schema": prompts.schema_text(MatchAssessment),
        },
    )


def comparison_query(requirements: JobRequirements) -> str:
    """The fixed query plus the requirement labels (spec section 7)."""
    labels = " ".join(requirement.label for requirement in all_requirements(requirements))
    return COMPARISON_QUERY + " " + labels


def run_comparison(
    llm, requirements: JobRequirements, profile: CandidateProfile, cv_text: str, index: GuidelineIndex | None
) -> AgentStep:
    """Decide met / partial / missing for every requirement, with quotes."""
    template = prompts.COMPARISON
    guideline_text, retrieved = retrieve(index, comparison_query(requirements))
    prompt = build_comparison_prompt(requirements, profile, cv_text, guideline_text)
    assessment, meta = llm.generate_json(
        "comparison", template.system, prompt, MatchAssessment, template.temperature, template.version
    )
    return AgentStep(align_results(assessment, requirements), meta, retrieved)


def run_comparison_revision(
    llm,
    requirements: JobRequirements,
    profile: CandidateProfile,
    cv_text: str,
    index: GuidelineIndex | None,
    previous: MatchAssessment,
    failing_ids: list[str],
) -> AgentStep:
    """Revision mode: send the Comparison Agent back once with the ids whose quotes failed."""
    template = prompts.COMPARISON
    guideline_text, retrieved = retrieve(index, comparison_query(requirements))
    prompt = build_comparison_prompt(requirements, profile, cv_text, guideline_text)
    prompt += "\n\n" + prompts.fill(
        prompts.COMPARISON_REVISION_INSTRUCTIONS,
        {
            "previous_answer": prompts.neutralise_tags(previous.model_dump_json(indent=1)),
            "failing_ids": ", ".join(failing_ids),
        },
    )
    revised, meta = llm.generate_json(
        "comparison_revision", template.system, prompt, MatchAssessment, template.temperature, template.version
    )
    revised = align_results(revised, requirements)
    return AgentStep(merge_revision(previous, revised, failing_ids), meta, retrieved)


def merge_revision(previous: MatchAssessment, revised: MatchAssessment, failing_ids: list[str]) -> MatchAssessment:
    """Accept the revision ONLY for the requirement ids that failed the quote check.

    Why: the model is told to keep other results unchanged, but a model sometimes
    rewrites everything (we saw a local model drop good quotes). Results that already
    passed the check are kept exactly as they were. Strengths and concerns are kept too.
    """
    revised_by_id = {result.requirement_id: result for result in revised.results}
    merged_results: list[RequirementResult] = []
    for result in previous.results:
        if result.requirement_id in failing_ids and result.requirement_id in revised_by_id:
            merged_results.append(revised_by_id[result.requirement_id])
        else:
            merged_results.append(result)
    return MatchAssessment(results=merged_results, strengths=previous.strengths, concerns=previous.concerns)


# ===========================================================================
# Guardrail Reviewer (code, no AI) (spec 5.5)
# ===========================================================================


def check_quotes(assessment: MatchAssessment, cv_text: str) -> list[str]:
    """Step 1: mark each met/partial result as verified or not. Returns the failing ids."""
    failing: list[str] = []
    for result in assessment.results:
        if result.status in ("met", "partial"):
            result.verified = guardrails.is_quote_verified(result.evidence, cv_text)
            if not result.verified:
                failing.append(result.requirement_id)
        else:
            result.verified = None  # nothing to verify for "missing"
    return failing


def downgrade_unverified(assessment: MatchAssessment) -> list[str]:
    """Step 3: a met/partial result whose quote still wasn't found becomes missing (§3.2)."""
    downgraded: list[str] = []
    for result in assessment.results:
        if result.status in ("met", "partial") and result.verified is False:
            result.original_status = result.status
            result.status = "missing"
            result.reasoning += " [Changed to missing by the reviewer: the quote was not found in the CV (§3.2).]"
            downgraded.append(result.requirement_id)
    return downgraded


def cap_by_minimum_years(
    assessment: MatchAssessment, requirements: JobRequirements, relevant_years: float | None
) -> None:
    """Step 4: below the minimum years, "met" is capped at "partial" (§4.4)."""
    if relevant_years is None:
        return
    requirement_by_id = {requirement.id: requirement for requirement in all_requirements(requirements)}
    for result in assessment.results:
        requirement = requirement_by_id.get(result.requirement_id)
        if requirement is None or requirement.min_years is None:
            continue
        if result.status == "met" and relevant_years < requirement.min_years:
            result.original_status = result.status
            result.status = "partial"
            result.reasoning += (
                f" [Capped at partial by the reviewer: {relevant_years} years of relevant experience "
                f"is below the minimum of {requirement.min_years:g} (§4.4).]"
            )
            if "§4.4" not in result.guideline_refs:
                result.guideline_refs.append("§4.4")


def role_line_quote(profile: CandidateProfile, cv_text: str) -> str | None:
    """A CV line naming a marketing role and its start date, e.g. "Digital Marketing Executive, X (Sep 2020 - Mar 2022)".

    Used as evidence for a minimum-years requirement. Returns None if no such line is found.
    """
    for role in profile.roles:
        if not role.is_marketing_role or not role.start:
            continue
        for line in cv_text.splitlines():
            if role.title.lower() in line.lower() and role.start.lower() in line.lower():
                quote = line.strip()
                if guardrails.is_quote_verified(quote, cv_text):
                    return quote
    return None


def raise_by_minimum_years(
    assessment: MatchAssessment, requirements: JobRequirements, profile: CandidateProfile, cv_text: str, today: date
) -> None:
    """Step 4b: when the CV's dates prove enough years, "missing" becomes "met" (§4.4).

    Seen with qwen2.5: Kevin has 6.2 years by his dates, yet the 3-years must-have came back missing.
    Only years that CODE worked out from the dates count (never the model's estimate), and the
    evidence is a role line that is verified against the CV like any other quote.
    """
    computed = guardrails.compute_experience_years(profile.roles, today)
    if computed is None:
        return
    quote = role_line_quote(profile, cv_text)
    if quote is None:
        return
    requirement_by_id = {requirement.id: requirement for requirement in all_requirements(requirements)}
    for result in assessment.results:
        requirement = requirement_by_id.get(result.requirement_id)
        if requirement is None or requirement.min_years is None:
            continue
        if result.status == "missing" and computed.relevant >= requirement.min_years:
            result.original_status = result.status
            result.status = "met"
            result.evidence = quote
            result.verified = True
            result.reasoning += (
                f" [Raised to met by the reviewer: the CV's dates give {computed.relevant} years of relevant "
                f"experience, at least the minimum of {requirement.min_years:g} (§4.4).]"
            )
            if "§4.4" not in result.guideline_refs:
                result.guideline_refs.append("§4.4")


def remove_gap_concerns(assessment: MatchAssessment) -> None:
    """Step 5: career gaps are never a concern (§2.3). They become a neutral question instead."""
    assessment.concerns = [concern for concern in assessment.concerns if not guardrails.mentions_career_gap(concern)]


def build_flags(
    intake: IntakeResult, profile: CandidateProfile, requirements: JobRequirements, downgraded_ids: list[str]
) -> list[Flag]:
    """Step 6: every flag for the candidate, from intake and from the profile."""
    flags: list[Flag] = []
    flags.extend(guardrails.intake_flags(intake))
    flags.extend(guardrails.missing_info_flags(profile))
    flags.extend(guardrails.check_salary(profile.salary_expectation, requirements.salary_max))
    flags.extend(guardrails.check_location(profile.location, profile.phone))
    flags.extend(guardrails.career_gap_flags(profile.career_gaps))
    if downgraded_ids:
        flags.append(
            Flag(
                code="unverified_evidence",
                severity="warning",
                message="Quotes for " + ", ".join(downgraded_ids)
                + " weren't found in the CV, so they were marked missing. Needs attention.",
                guideline="§8.4",
            )
        )
    return flags


@dataclass
class ReviewOutcome:
    assessment: MatchAssessment
    flags: list[Flag]
    missing_info: list[str]
    needs_attention: bool
    downgraded_ids: list[str]


def finish_review(
    assessment: MatchAssessment,
    requirements: JobRequirements,
    profile: CandidateProfile,
    intake: IntakeResult,
    today: date | None = None,
) -> ReviewOutcome:
    """Steps 3 to 6 of the Guardrail Reviewer, run after any revision."""
    downgraded = downgrade_unverified(assessment)
    cap_by_minimum_years(assessment, requirements, profile.relevant_years_experience)
    raise_by_minimum_years(assessment, requirements, profile, intake.clean_text, today or date.today())
    remove_gap_concerns(assessment)
    flags = build_flags(intake, profile, requirements, downgraded)
    return ReviewOutcome(
        assessment=assessment,
        flags=flags,
        missing_info=guardrails.find_missing_info(profile),
        needs_attention=len(downgraded) > 0,
        downgraded_ids=downgraded,
    )


# ===========================================================================
# Report Agent, candidate mode (spec 5.6)
# ===========================================================================


def gap_question(gap: str) -> InterviewQuestion:
    return InterviewQuestion(
        question=(
            f"Your CV shows a period away from paid work ({gap}). Is there anything from that time "
            "you'd like to share that is relevant to this role?"
        ),
        purpose="Neutral clarification of a career gap (§2.3). Not a weakness.",
    )


def work_permit_question() -> InterviewQuestion:
    return InterviewQuestion(
        question="You're currently based outside Mauritius. What is your work permit status, and what would your relocation timeline be?",
        purpose="Work authorisation and relocation (§6.2).",
    )


def weak_requirement_questions(
    assessment: MatchAssessment, requirements: JobRequirements
) -> list[InterviewQuestion]:
    """Template questions for the weakest requirements: missing must-haves first, then partial ones."""
    requirement_by_id = {requirement.id: requirement for requirement in all_requirements(requirements)}
    status_by_id = {result.requirement_id: result.status for result in assessment.results}

    ordered_ids: list[str] = []
    for wanted_status in ("missing", "partial"):
        for group in (requirements.must_have, requirements.nice_to_have):
            for requirement in group:
                if status_by_id.get(requirement.id) == wanted_status:
                    ordered_ids.append(requirement.id)

    questions = []
    for requirement_id in ordered_ids:
        label = requirement_by_id[requirement_id].label
        questions.append(
            InterviewQuestion(
                question=f"Tell me about a time you used {label.lower()} in your work. What did you do, and what was the result?",
                purpose=f"Clarify {requirement_id} ({label}), assessed as {status_by_id[requirement_id]}.",
            )
        )
    return questions


# Last-resort questions, used only when there are no weak requirements left to ask about.
GENERAL_QUESTIONS: list[InterviewQuestion] = [
    InterviewQuestion(
        question="Tell me about a marketing campaign you're most proud of. What was your role, and what were the results?",
        purpose="General: campaign experience and impact.",
    ),
    InterviewQuestion(
        question="Describe how you decide which channels and budget to use for a new campaign.",
        purpose="General: planning and prioritisation.",
    ),
    InterviewQuestion(
        question="How do you keep up with changes on digital marketing platforms?",
        purpose="General: learning and staying current.",
    ),
]


def mentions_any(questions: list[InterviewQuestion], pattern: str) -> bool:
    for item in questions:
        if re.search(pattern, item.question, flags=re.IGNORECASE):
            return True
    return False


def finish_candidate_report(
    report: CandidateReport,
    assessment: MatchAssessment,
    requirements: JobRequirements,
    profile: CandidateProfile,
    flags: list[Flag],
) -> list[str]:
    """Code checks after the Report Agent. Returns the texts removed by the bias filter.

    1. Remove any summary sentence, skill or question (and any strength or concern)
       that mentions a protected term.
    2. Add a neutral question for each career gap, and a work-permit question for
       candidates outside Mauritius, if the AI didn't include them.
    3. Keep 3 to 5 questions, filling from templates for the weakest requirements.
    """
    removed: list[str] = []

    report.summary, removed_sentences = guardrails.filter_protected_sentences(report.summary)
    removed.extend(removed_sentences)
    report.relevant_experience, removed_sentences = guardrails.filter_protected_sentences(report.relevant_experience)
    removed.extend(removed_sentences)

    kept_skills, removed_skills = guardrails.filter_protected_items(report.key_skills)
    report.key_skills = kept_skills[:MAX_KEY_SKILLS]
    removed.extend(removed_skills)

    assessment.strengths, removed_items = guardrails.filter_protected_items(assessment.strengths)
    removed.extend(removed_items)
    assessment.concerns, removed_items = guardrails.filter_protected_items(assessment.concerns)
    removed.extend(removed_items)

    ai_questions: list[InterviewQuestion] = []
    for item in report.interview_questions:
        if guardrails.contains_protected_term(item.question) or guardrails.contains_protected_term(item.purpose):
            removed.append(item.question)
        else:
            ai_questions.append(item)

    # Required template questions, added only if the AI didn't cover them.
    required: list[InterviewQuestion] = []
    if profile.career_gaps and not mentions_any(ai_questions, r"\bgap\b|\bbreak\b|away from"):
        required.append(gap_question(profile.career_gaps[0]))
    outside = any(flag.code == "outside_mauritius" for flag in flags)
    if outside and not mentions_any(ai_questions, r"permit|relocat"):
        required.append(work_permit_question())

    # The required questions always stay; AI questions fill the rest, up to 5.
    room_for_ai = MAX_QUESTIONS - len(required)
    questions = ai_questions[:room_for_ai] + required

    # Too few? Add template questions for the weakest requirements, then general ones.
    for template_question in weak_requirement_questions(assessment, requirements) + GENERAL_QUESTIONS:
        if len(questions) >= MIN_QUESTIONS:
            break
        questions.append(template_question)

    report.interview_questions = questions
    return removed


def candidate_report_prompt(
    profile: CandidateProfile,
    assessment: MatchAssessment,
    requirements: JobRequirements,
    score: ScoreBreakdown,
    missing_info: list[str],
    flags: list[Flag],
    guideline_text: str,
) -> str:
    label_by_id = {requirement.id: requirement.label for requirement in all_requirements(requirements)}
    assessment_rows = []
    for result in assessment.results:
        assessment_rows.append(
            {
                "id": result.requirement_id,
                "requirement": label_by_id.get(result.requirement_id, ""),
                "status": result.status,
                "evidence": result.evidence,
            }
        )
    assessment_data = {
        "results": assessment_rows,
        "strengths": assessment.strengths,
        "concerns": assessment.concerns,
    }
    profile_data = profile.model_dump(exclude={"email", "phone", "references"})
    return prompts.fill(
        prompts.REPORT_CANDIDATE.user,
        {
            "profile": prompts.neutralise_tags(prompts.as_json(profile_data)),
            "assessment": prompts.neutralise_tags(prompts.as_json(assessment_data)),
            "score": prompts.as_json(score.model_dump()),
            "computed_years": computed_years_text(profile),
            "missing_information": prompts.as_json(missing_info),
            "flags": prompts.as_json([flag.message for flag in flags]),
            "guidelines": guideline_text,
            "schema": prompts.schema_text(CandidateReport),
        },
    )


def run_candidate_report(
    llm,
    profile: CandidateProfile,
    assessment: MatchAssessment,
    requirements: JobRequirements,
    score: ScoreBreakdown,
    missing_info: list[str],
    flags: list[Flag],
    index: GuidelineIndex | None,
) -> tuple[AgentStep, list[str]]:
    """Write the candidate brief. Returns the step and the texts removed by the bias filter."""
    template = prompts.REPORT_CANDIDATE
    guideline_text, retrieved = retrieve(index, REPORT_QUERY)
    prompt = candidate_report_prompt(profile, assessment, requirements, score, missing_info, flags, guideline_text)
    report, meta = llm.generate_json(
        "report_candidate", template.system, prompt, CandidateReport, template.temperature, template.version
    )
    removed = finish_candidate_report(report, assessment, requirements, profile, flags)
    return AgentStep(report, meta, retrieved), removed


# ===========================================================================
# Report Agent, shortlist mode (spec 5.6)
# ===========================================================================


def run_shortlist_overview(llm, job_title: str, shortlisted: list[dict]) -> AgentStep:
    """Write the shortlist overview. `shortlisted` holds ONLY shortlisted candidates.

    Each dict: name, band, score, summary, strengths, missing_information, recruiter_notes.
    """
    template = prompts.REPORT_SHORTLIST
    prompt = prompts.fill(
        template.user,
        {
            "job_title": prompts.neutralise_tags(job_title),
            "candidates": prompts.neutralise_tags(prompts.as_json(shortlisted)),
            "schema": prompts.schema_text(ShortlistOverview),
        },
    )
    overview, meta = llm.generate_json(
        "report_shortlist", template.system, prompt, ShortlistOverview, template.temperature, template.version
    )
    overview.overview, removed = guardrails.filter_protected_sentences(overview.overview)
    overview.points_to_discuss, removed_points = guardrails.filter_protected_items(overview.points_to_discuss)
    return AgentStep(overview, meta, [])
