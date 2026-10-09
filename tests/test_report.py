"""Tests for core/report.py: only shortlisted candidates reach the report (AC9), and the downloads are safe.

The run is built by the real Supervisor with the FakeLLM, so no real AI is called.
"""

from datetime import date

import pytest

from core.guardrails import run_intake
from core.documents import content_hash
from core.rag import build_index
from core.report import (
    build_report_markdown,
    count_decisions,
    markdown_to_html_page,
    overview_input,
    shortlisted_candidates,
    with_footer,
)
from core.schemas import JobRequirements, ShortlistOverview
from core.supervisor import create_run, run_screening
from tests.conftest import CV_DIR, SAMPLE_DIR
from tests.fake_llm import SAMPLE_REQUIREMENTS, FakeLLM


@pytest.fixture(scope="module")
def run():
    """A screened run of the 6 sample CVs, made with the fake model."""
    from core.documents import read_document_file

    requirements = JobRequirements.model_validate(SAMPLE_REQUIREMENTS)
    index = build_index((SAMPLE_DIR / "hiring_guidelines.md").read_text(encoding="utf-8"))
    documents = []
    for path in sorted(CV_DIR.iterdir()):
        documents.append(run_intake(path.name, read_document_file(path), content_hash(path.read_bytes())))
    screened = create_run(requirements, "fake", "fake-model")
    run_screening(screened, documents, FakeLLM(), index, on_event=None, save_run=None, today=date(2026, 10, 9))
    return screened


def by_name(run, name: str):
    return next(c for c in run.candidates if c.profile and c.profile.name.startswith(name))


def decision(candidate, choice: str, reason: str = "") -> dict:
    return {candidate.candidate_id: {"decision": choice, "reason": reason}}


@pytest.fixture
def decisions(run) -> dict:
    """Sarah and Kevin shortlisted, Aisha on hold, Ryan rejected; the rest pending."""
    result = {}
    result.update(decision(by_name(run, "Sarah"), "Shortlist", "Strong social media portfolio"))
    result.update(decision(by_name(run, "Kevin"), "Shortlist"))
    result.update(decision(by_name(run, "Aisha"), "Hold", "Waiting for references"))
    result.update(decision(by_name(run, "Ryan"), "Reject", "No digital marketing experience"))
    return result


def test_counts_cover_every_decision(run, decisions) -> None:
    counts = count_decisions(run, decisions)
    assert counts == {"Shortlist": 2, "Hold": 1, "Reject": 1, "Pending": 2}


def test_only_shortlisted_candidates_are_in_the_report(run, decisions) -> None:
    """AC9 / FR-P5: hold, rejected and pending candidates never appear by name."""
    overview = ShortlistOverview(overview="Two candidates were shortlisted.", points_to_discuss=["Notice periods"])
    text = build_report_markdown(run, decisions, overview, "Alisha", "09 Oct 2026")
    assert "## Sarah" in text and "## Kevin" in text
    for candidate in run.candidates:
        if candidate.candidate_id not in (by_name(run, "Sarah").candidate_id, by_name(run, "Kevin").candidate_id):
            assert candidate.profile.name not in text
    assert "Shortlisted: 2 · On hold: 1 · Rejected: 1 · Pending: 2" in text


def test_report_sections_follow_the_brief_format(run, decisions) -> None:
    text = build_report_markdown(run, decisions, None, "Alisha", "09 Oct 2026")
    for heading in ["**Candidate**", "**Relevant Experience**", "**Key Skills**",
                    "**Missing Information**", "**Questions for Interview**"]:
        assert text.count(heading) == 2  # once per shortlisted candidate
    assert "Strong social media portfolio" in text  # the recruiter's notes are included
    assert text.rstrip().endswith("Prepared with AI assistance. All decisions made by Alisha on 09 Oct 2026.")


def test_overview_input_holds_only_shortlisted_candidates(run, decisions) -> None:
    rows = overview_input(run, decisions)
    assert sorted(row["name"].split()[0] for row in rows) == ["Kevin", "Sarah"]
    assert set(rows[0]) == {"name", "band", "score", "summary", "strengths", "missing_information", "recruiter_notes"}


def test_overview_prompt_never_mentions_other_candidates(run, decisions) -> None:
    from core.agents import run_shortlist_overview

    fake = FakeLLM()
    run_shortlist_overview(fake, "Marketing Executive", overview_input(run, decisions))
    sent = fake.everything_sent()
    assert "Sarah" in sent
    for name in ["Aisha", "Ryan", "Jean-Marc"]:
        assert name not in sent


def test_no_shortlist_means_no_candidate_sections(run) -> None:
    assert shortlisted_candidates(run, {}) == []
    text = build_report_markdown(run, {}, None, "Alisha", "09 Oct 2026")
    assert "**Candidate**" not in text


def test_footer_is_replaced_not_duplicated() -> None:
    edited = "# Shortlist\n\nSome text\n\n---\n\nPrepared with AI assistance. All decisions made by Someone on 01 Jan 2026.\n"
    final = with_footer(edited, "Alisha", "09 Oct 2026")
    assert final.count("Prepared with AI assistance.") == 1
    assert final.rstrip().endswith("All decisions made by Alisha on 09 Oct 2026.")
    assert "Some text" in final


def test_html_download_escapes_text_and_keeps_structure() -> None:
    page = markdown_to_html_page("# Title\n\n**Key Skills**\n- SEO <script>alert(1)</script>\n1. First\n", "Shortlist")
    assert "<h1>Title</h1>" in page
    assert "<strong>Key Skills</strong>" in page
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert "<ul>" in page and "<ol>" in page
    assert page.startswith("<!doctype html>")
