"""Scoring: turns met / partial / missing results into a score and a band (spec section 9).

Why this file exists:
- The AI never gives the score. It only says met, partial or missing (with a quote).
  This file does the maths, so the same results always give the same score.
- Every number comes from the hiring guidelines, section 4 (the scoring rubric).
"""

from __future__ import annotations

from core.schemas import Band, JobRequirements, Requirement, RequirementResult, ScoreBreakdown

# --- The rubric (Hiring Guidelines §4) --------------------------------------

# §4.1: Met = full credit, Partial = half credit, Missing = no credit.
CREDIT_FOR_STATUS: dict[str, float] = {"met": 1.0, "partial": 0.5, "missing": 0.0}

# §4.2: must-haves carry 70% of the score, nice-to-haves 30%.
MUST_HAVE_SHARE: float = 0.70
NICE_TO_HAVE_SHARE: float = 0.30

# §4.3: recommendation bands.
STRONG_MATCH_MIN_SCORE: float = 75.0
POSSIBLE_MATCH_MIN_SCORE: float = 55.0

STRONG_MATCH: Band = "Strong match"
POSSIBLE_MATCH: Band = "Possible match"
NOT_A_MATCH: Band = "Not a match for this role"


def status_by_requirement(results: list[RequirementResult]) -> dict[str, str]:
    """Map each requirement id to its status, e.g. {"M1": "met", "M2": "partial"}."""
    statuses: dict[str, str] = {}
    for result in results:
        statuses[result.requirement_id] = result.status
    return statuses


def weighted_fraction(requirements: list[Requirement], statuses: dict[str, str]) -> float | None:
    """Σ(weight × credit) / Σ(weight) for a group of requirements, from 0.0 to 1.0.

    A requirement with no result counts as missing. Returns None for an empty group.
    """
    if not requirements:
        return None
    earned = 0.0
    possible = 0.0
    for requirement in requirements:
        status = statuses.get(requirement.id, "missing")
        earned += requirement.weight * CREDIT_FOR_STATUS[status]
        possible += requirement.weight
    return earned / possible


def band_for_score(overall: float) -> Band:
    """§4.3: 75 or above = Strong match, 55 to 74.9 = Possible match, below 55 = Not a match."""
    if overall >= STRONG_MATCH_MIN_SCORE:
        return STRONG_MATCH
    if overall >= POSSIBLE_MATCH_MIN_SCORE:
        return POSSIBLE_MATCH
    return NOT_A_MATCH


def must_have_gaps(requirements: JobRequirements, statuses: dict[str, str]) -> list[str]:
    """The labels of the must-haves marked missing (shown on the candidate card)."""
    gaps: list[str] = []
    for requirement in requirements.must_have:
        if statuses.get(requirement.id, "missing") == "missing":
            gaps.append(requirement.label)
    return gaps


def score_candidate(requirements: JobRequirements, results: list[RequirementResult]) -> ScoreBreakdown:
    """Work out the must-have %, nice-to-have %, overall score and band for one candidate."""
    statuses = status_by_requirement(results)
    must_fraction = weighted_fraction(requirements.must_have, statuses)
    nice_fraction = weighted_fraction(requirements.nice_to_have, statuses)

    # A job always has at least one must-have (checked when requirements are made).
    if must_fraction is None:
        must_fraction = 0.0

    if nice_fraction is None:
        # No nice-to-haves: the must-haves count for 100% (spec section 9).
        overall_fraction = must_fraction
        nice_fraction = 0.0
    else:
        overall_fraction = MUST_HAVE_SHARE * must_fraction + NICE_TO_HAVE_SHARE * nice_fraction

    overall = round(100 * overall_fraction, 1)
    return ScoreBreakdown(
        must_have_pct=round(100 * must_fraction, 1),
        nice_to_have_pct=round(100 * nice_fraction, 1),
        overall=overall,
        band=band_for_score(overall),
        must_have_gaps=must_have_gaps(requirements, statuses),
    )
