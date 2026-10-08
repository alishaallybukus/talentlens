"""Tests for core/scoring.py: the rubric gives the expected scores and bands."""

import pytest

from core.schemas import JobRequirements, Requirement, RequirementResult
from core.scoring import band_for_score, score_candidate


def make_requirements(must_count: int = 7, nice_count: int = 7) -> JobRequirements:
    """A job with M1..Mn and N1..Nn, all weight 2 (like the worked example)."""
    must = [Requirement(id=f"M{i}", label=f"Must {i}", description="") for i in range(1, must_count + 1)]
    nice = [Requirement(id=f"N{i}", label=f"Nice {i}", description="") for i in range(1, nice_count + 1)]
    return JobRequirements(title="Marketing Executive", must_have=must, nice_to_have=nice)


def make_results(statuses: dict[str, str]) -> list[RequirementResult]:
    return [
        RequirementResult(requirement_id=requirement_id, status=status, reasoning="test")
        for requirement_id, status in statuses.items()
    ]


def all_with_status(requirements: JobRequirements, status: str) -> list[RequirementResult]:
    ids = [r.id for r in requirements.must_have + requirements.nice_to_have]
    return make_results({requirement_id: status for requirement_id in ids})


def test_all_met_scores_100() -> None:
    requirements = make_requirements()
    score = score_candidate(requirements, all_with_status(requirements, "met"))
    assert score.overall == 100.0
    assert score.band == "Strong match"
    assert score.must_have_gaps == []


def test_all_missing_scores_0() -> None:
    requirements = make_requirements()
    score = score_candidate(requirements, all_with_status(requirements, "missing"))
    assert score.overall == 0.0
    assert score.band == "Not a match for this role"
    assert len(score.must_have_gaps) == 7


def test_worked_example_scores_84_3() -> None:
    # Spec section 9: 6 met + 1 partial must-haves; 4 met, 1 partial, 2 missing nice-to-haves.
    statuses = {f"M{i}": "met" for i in range(1, 7)}
    statuses["M7"] = "partial"
    statuses.update({"N1": "met", "N2": "met", "N3": "met", "N4": "met", "N5": "partial", "N6": "missing", "N7": "missing"})
    score = score_candidate(make_requirements(), make_results(statuses))
    assert score.must_have_pct == 92.9
    assert score.nice_to_have_pct == 64.3
    assert score.overall == 84.3
    assert score.band == "Strong match"


@pytest.mark.parametrize(
    "overall, band",
    [
        (54.9, "Not a match for this role"),
        (55.0, "Possible match"),
        (74.9, "Possible match"),
        (75.0, "Strong match"),
    ],
)
def test_band_edges(overall, band) -> None:
    assert band_for_score(overall) == band


def test_weights_change_the_result() -> None:
    requirements = make_requirements(must_count=2, nice_count=0)
    results = make_results({"M1": "met", "M2": "missing"})
    equal_weights = score_candidate(requirements, results).overall

    requirements.must_have[0].weight = 3  # M1 now matters more
    requirements.must_have[1].weight = 1
    heavier_m1 = score_candidate(requirements, results).overall

    assert equal_weights == 50.0
    assert heavier_m1 == 75.0


def test_missing_result_counts_as_missing() -> None:
    requirements = make_requirements(must_count=2, nice_count=0)
    score = score_candidate(requirements, make_results({"M1": "met"}))
    assert score.overall == 50.0
    assert score.must_have_gaps == ["Must 2"]


def test_no_nice_to_haves_means_must_haves_count_100_percent() -> None:
    requirements = make_requirements(must_count=3, nice_count=0)
    score = score_candidate(requirements, all_with_status(requirements, "met"))
    assert score.overall == 100.0
    assert score.nice_to_have_pct == 0.0


def test_same_input_always_gives_same_score() -> None:
    requirements = make_requirements()
    results = all_with_status(requirements, "partial")
    first = score_candidate(requirements, results)
    second = score_candidate(requirements, results)
    assert first == second
    assert first.overall == 50.0
