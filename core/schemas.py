"""Every data format TalentLens uses, written as Pydantic models (spec section 6).

Why this file exists:
- Every AI answer must match one of these shapes. Pydantic checks the AI's JSON
  for us and raises an error if a field is missing or has the wrong type.
- The rest of the code passes these objects around, so everyone agrees on
  what a "requirement" or a "flag" looks like.

A field written as `str | None = None` is optional: it may be empty (None).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Allowed values, written once so they can't be misspelled elsewhere.
Status = Literal["met", "partial", "missing"]
Band = Literal["Strong match", "Possible match", "Not a match for this role"]
Severity = Literal["info", "warning", "critical"]
TraceStatus = Literal["started", "done", "revised", "warning", "error"]


class SchemaModel(BaseModel):
    """The base for every model below.

    extra="ignore" means: if the AI adds fields we didn't ask for, drop them
    quietly instead of failing.
    """

    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------------
# Job requirements (Job Analyst Agent output)
# ---------------------------------------------------------------------------


class Requirement(SchemaModel):
    """One line of the requirements checklist, e.g. "At least 3 years of digital marketing"."""

    id: str  # "M1".."M7" for must-haves, "N1".."N7" for nice-to-haves
    label: str
    description: str
    keywords: list[str] = Field(default_factory=list)
    min_years: float | None = None
    weight: int = Field(default=2, ge=1, le=3)


class JobRequirements(SchemaModel):
    """The whole checklist for one job, plus the salary band."""

    title: str
    company: str | None = None
    location: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    currency: str | None = None
    must_have: list[Requirement] = Field(default_factory=list)
    nice_to_have: list[Requirement] = Field(default_factory=list)
    preference_notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Candidate profile (CV Analyst Agent output)
# ---------------------------------------------------------------------------


class Role(SchemaModel):
    """One job (or career break) listed on a CV. Dates are kept as written, e.g. "Feb 2022"."""

    title: str
    company: str | None = None
    start: str | None = None
    end: str | None = None
    is_marketing_role: bool = False
    highlights: list[str] = Field(default_factory=list)


class CandidateProfile(SchemaModel):
    """The facts taken from one CV, without any judgement."""

    name: str
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    headline: str | None = None
    total_years_experience: float | None = None
    relevant_years_experience: float | None = None
    roles: list[Role] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    salary_expectation: str | None = None
    notice_period: str | None = None
    right_to_work: str | None = None
    references: str | None = None
    career_gaps: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Match assessment (Comparison Agent output)
# ---------------------------------------------------------------------------


class RequirementResult(SchemaModel):
    """Whether one requirement is met, partial or missing, with the CV quote as evidence."""

    requirement_id: str
    status: Status
    evidence: str | None = None
    reasoning: str
    guideline_refs: list[str] = Field(default_factory=list)
    verified: bool | None = None  # set by code: was the quote found in the CV?
    original_status: str | None = None  # set by code if the status was changed


class MatchAssessment(SchemaModel):
    """The full comparison of one candidate against all requirements."""

    results: list[RequirementResult] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Reports (Report Agent output)
# ---------------------------------------------------------------------------


class InterviewQuestion(SchemaModel):
    question: str
    purpose: str


class CandidateReport(SchemaModel):
    """The brief-format write-up for one candidate."""

    summary: str
    relevant_experience: str
    key_skills: list[str] = Field(default_factory=list)
    interview_questions: list[InterviewQuestion] = Field(default_factory=list)


class ShortlistOverview(SchemaModel):
    """The AI-written overview at the top of the shortlist report."""

    overview: str
    points_to_discuss: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Code-made results (no AI)
# ---------------------------------------------------------------------------


class Flag(SchemaModel):
    """Something the recruiter should notice, e.g. "salary above band"."""

    code: str
    severity: Severity
    message: str
    guideline: str | None = None  # the guideline clause, e.g. "§6.1"


class ScoreBreakdown(SchemaModel):
    """The score, worked out by code in core/scoring.py (never by the AI)."""

    must_have_pct: float
    nice_to_have_pct: float
    overall: float
    band: Band
    must_have_gaps: list[str] = Field(default_factory=list)


class IntakeResult(SchemaModel):
    """A CV after intake: cleaned text plus what the guardrails removed.

    `redactions` holds labels only (e.g. "Date of birth"), never the removed values.
    """

    file_name: str
    content_hash: str
    clean_text: str
    word_count: int
    redactions: list[str] = Field(default_factory=list)
    quarantined_text: list[str] = Field(default_factory=list)


class CandidateResult(SchemaModel):
    """Everything we know about one screened candidate."""

    candidate_id: str
    file_name: str
    profile: CandidateProfile | None = None
    assessment: MatchAssessment | None = None
    report: CandidateReport | None = None
    score: ScoreBreakdown | None = None
    missing_info: list[str] = Field(default_factory=list)
    flags: list[Flag] = Field(default_factory=list)
    redactions: list[str] = Field(default_factory=list)
    quarantined_text: list[str] = Field(default_factory=list)
    revised: bool = False
    needs_attention: bool = False
    error: str | None = None


class TraceEvent(SchemaModel):
    """One step in the "behind the scenes" trace, e.g. "CV Analyst finished for Sarah"."""

    ts: str
    agent: str
    candidate: str | None = None
    action: str
    status: TraceStatus
    duration_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cache_hit: bool = False
    model: str | None = None
    prompt_version: str | None = None
    retrieved: list[str] = Field(default_factory=list)
    detail: str | None = None


class RunResult(SchemaModel):
    """A whole screening run: the requirements, every candidate, and the trace."""

    run_id: str
    created_at: str
    provider: str
    model: str
    requirements: JobRequirements
    candidates: list[CandidateResult] = Field(default_factory=list)
    trace: list[TraceEvent] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)
