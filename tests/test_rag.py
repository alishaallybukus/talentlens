"""Tests for core/rag.py: the guidelines split into clauses, and BM25 finds the right ones."""

import pytest

from core.rag import build_index, clause_ids, format_clause, split_into_clauses, tokenize
from tests.conftest import SAMPLE_DIR


@pytest.fixture
def guidelines_text() -> str:
    return (SAMPLE_DIR / "hiring_guidelines.md").read_text(encoding="utf-8")


def test_guidelines_split_into_clauses_with_ids(guidelines_text) -> None:
    clauses = split_into_clauses(guidelines_text)
    ids = [clause.id for clause in clauses]
    # 27 numbered clauses, plus §0 (introduction) and §1 (Purpose, which has no numbered items).
    assert len(clauses) == 29
    assert "§1" in ids
    assert "§2.3" in ids
    assert "§4.4" in ids
    assert "§9.3" in ids
    assert len(ids) == len(set(ids))  # every id is unique


def test_clause_keeps_its_section_title(guidelines_text) -> None:
    index = build_index(guidelines_text)
    clause = index.get("§4.4")
    assert clause.section_title == "Scoring rubric"
    assert clause.text.startswith("Years of experience")
    assert format_clause(clause).startswith("[§4.4] (Scoring rubric) Years of experience")


def test_tokenizer_drops_stop_words_and_plurals() -> None:
    assert tokenize("The career gaps of candidates") == ["career", "gap", "candidate"]


def test_salary_above_band_finds_6_1_in_top_3(guidelines_text) -> None:
    results = build_index(guidelines_text).search("salary above band", top_k=3)
    assert "§6.1" in clause_ids(results)


def test_interview_questions_finds_section_7(guidelines_text) -> None:
    results = build_index(guidelines_text).search("interview questions", top_k=3)
    assert any(clause_id.startswith("§7.") for clause_id in clause_ids(results))


def test_career_gap_finds_2_3(guidelines_text) -> None:
    results = build_index(guidelines_text).search("career gap", top_k=3)
    assert clause_ids(results)[0] == "§2.3"


def test_search_returns_at_most_top_k(guidelines_text) -> None:
    results = build_index(guidelines_text).search("evidence quote met partial missing transferable experience minimum years scoring")
    assert 1 <= len(results) <= 5


def test_unrelated_query_returns_nothing(guidelines_text) -> None:
    assert build_index(guidelines_text).search("pineapple submarine") == []
