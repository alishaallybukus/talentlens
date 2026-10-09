"""Tests for eval/run_eval.py: the checks that compare a run with ground_truth.json.

The run is made by the real Supervisor with the FakeLLM, so no real AI is called.
"""

from datetime import date

import pytest

from core.rag import build_index
from core.schemas import Flag, JobRequirements
from core.supervisor import create_run, run_screening
from eval.run_eval import (
    check_flags,
    evaluate,
    load_cvs,
    load_ground_truth,
    markdown_table,
    missing_info_precision_recall,
    summarise,
)
from tests.conftest import SAMPLE_DIR
from tests.fake_llm import SAMPLE_REQUIREMENTS, FakeLLM


@pytest.fixture(scope="module")
def run():
    requirements = JobRequirements.model_validate(SAMPLE_REQUIREMENTS)
    index = build_index((SAMPLE_DIR / "hiring_guidelines.md").read_text(encoding="utf-8"))
    screened = create_run(requirements, "fake", "fake-model")
    run_screening(screened, load_cvs(None), FakeLLM(), index, today=date(2026, 10, 9))
    return screened


def test_all_seven_cvs_are_evaluated(run) -> None:
    truth = load_ground_truth()
    rows = evaluate(run, truth)
    assert len(rows) == 7
    assert {row["cv"] for row in rows} == set(truth)
    for row in rows:
        assert set(row) >= {"cv", "band", "missing_info", "flags", "seconds", "tokens", "passed", "problems"}


def test_missing_info_is_checked_exactly(run) -> None:
    """The fake CV Analyst reads labelled fields, so missing info should match the ground truth."""
    truth = load_ground_truth()
    rows = {row["cv"]: row for row in evaluate(run, truth)}
    assert "missing info differs" not in rows["Kevin_Ramdin_CV.docx"]["problems"]
    precision, recall = missing_info_precision_recall(run.candidates, truth)
    assert 0 <= precision <= 100 and 0 <= recall <= 100


def test_ryan_and_jean_marc_flags_are_found_by_code(run) -> None:
    """These flags come from code (intake and checks), so even the fake model gets them."""
    truth = load_ground_truth()
    rows = {row["cv"]: row for row in evaluate(run, truth)}
    assert "prompt_injection" in rows["Ryan_Chen_CV.pdf"]["flags"]
    assert "protected_info_redacted" in rows["Jean-Marc_Lebrun_CV.pdf"]["flags"]


def test_false_prompt_injection_flag_fails(run) -> None:
    sarah = next(c for c in run.candidates if c.file_name == "Sarah_Moutou_CV.pdf").model_copy(deep=True)
    sarah.flags.append(Flag(code="prompt_injection", severity="critical", message="x"))
    passed, problems = check_flags(sarah, {"flags": []})
    assert not passed and problems == ["false prompt_injection flag"]


def test_missing_expected_flag_fails(run) -> None:
    sarah = next(c for c in run.candidates if c.file_name == "Sarah_Moutou_CV.pdf")
    passed, problems = check_flags(sarah, {"flags": ["career_gap"]})
    assert not passed and problems == ["missing flag career_gap"]


def test_summary_and_markdown(run) -> None:
    truth = load_ground_truth()
    rows = evaluate(run, truth)
    summary = summarise(run, rows, truth)
    assert summary["passed"].endswith("of 7")
    result = {"provider": "fake", "model": "fake-model", "timestamp": "20261009_000000",
              "requirements_source": "test", "summary": summary, "cases": rows}
    table = markdown_table(result)
    assert "| CV | Band |" in table and "passed" in table
