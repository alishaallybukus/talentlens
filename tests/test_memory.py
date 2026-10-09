"""Tests for core/memory.py. Each test uses its own temporary database, never data/talentlens.db."""

import pytest

from core.memory import REJECTION_REASON_MESSAGE, DecisionError, Memory
from core.schemas import IntakeResult, JobRequirements, Requirement, RunResult


@pytest.fixture
def database_url(tmp_path) -> str:
    return "sqlite:///" + (tmp_path / "test.db").as_posix()


@pytest.fixture
def memory(database_url):
    memory = Memory(database_url)
    yield memory
    memory.close()


def make_requirements() -> JobRequirements:
    return JobRequirements(
        title="Marketing Executive",
        salary_min=45000,
        salary_max=60000,
        must_have=[Requirement(id="M1", label="Degree", description="Bachelor's degree")],
    )


def make_intake(file_name: str = "Sarah_Moutou_CV.pdf", content_hash: str = "abc") -> IntakeResult:
    return IntakeResult(
        file_name=file_name,
        content_hash=content_hash,
        clean_text="Sarah Moutou ...",
        word_count=2,
        redactions=["Date of birth"],
        quarantined_text=[],
    )


def make_run(run_id: str = "run-1", created_at: str = "2026-10-08T10:00:00+00:00") -> RunResult:
    return RunResult(
        run_id=run_id, created_at=created_at, provider="gemini", model="gemini-3.8-flash",
        requirements=make_requirements(),
    )


# --- Preferences ------------------------------------------------------------


def test_preferences_can_be_added_listed_and_removed(memory) -> None:
    first_id = memory.add_preference("TikTok experience matters a lot", actor="Recruiter")
    memory.add_preference("French copywriting is essential", actor="Recruiter")
    assert [p["text"] for p in memory.list_preferences()] == [
        "TikTok experience matters a lot",
        "French copywriting is essential",
    ]
    memory.delete_preference(first_id, actor="Recruiter")
    assert [p["text"] for p in memory.list_preferences()] == ["French copywriting is essential"]


def test_empty_preference_is_refused(memory) -> None:
    with pytest.raises(ValueError):
        memory.add_preference("   ", actor="Recruiter")


# --- Decisions --------------------------------------------------------------


def test_decision_is_saved_with_band_and_score(memory) -> None:
    memory.save_decision("run-1", "c1", "Sarah Moutou", "Shortlist", None, "Alisha", "Strong match", 88.6)
    saved = memory.get_decisions("run-1")["c1"]
    assert saved["decision"] == "Shortlist"
    assert saved["reviewer"] == "Alisha"
    assert saved["ai_band"] == "Strong match"
    assert saved["ai_score"] == 88.6


def test_decision_can_be_changed(memory) -> None:
    memory.save_decision("run-1", "c1", "Sarah Moutou", "Hold", None, "Alisha", "Strong match", 88.6)
    memory.save_decision("run-1", "c1", "Sarah Moutou", "Shortlist", None, "Alisha", "Strong match", 88.6)
    decisions = memory.get_decisions("run-1")
    assert len(decisions) == 1
    assert decisions["c1"]["decision"] == "Shortlist"


@pytest.mark.parametrize("reason", [None, "", "   ", "too short"])
def test_reject_without_a_proper_reason_is_blocked(memory, reason) -> None:
    with pytest.raises(DecisionError, match=r"Hiring Guidelines §8\.2"):
        memory.save_decision("run-1", "c6", "Ryan Chen", "Reject", reason, "Alisha", "Not a match for this role", 12.0)
    assert memory.get_decisions("run-1") == {}
    assert str(REJECTION_REASON_MESSAGE).startswith("A rejection needs a written")


def test_reject_with_a_reason_is_saved(memory) -> None:
    reason = "No digital marketing experience; must-haves M2 to M5 missing."
    memory.save_decision("run-1", "c6", "Ryan Chen", "Reject", reason, "Alisha", "Not a match for this role", 12.0)
    assert memory.get_decisions("run-1")["c6"]["reason"] == reason


def test_unknown_decision_is_blocked(memory) -> None:
    with pytest.raises(DecisionError):
        memory.save_decision("run-1", "c1", "Sarah Moutou", "Hire", None, "Alisha", None, None)


def test_reset_decisions_clears_only_that_run(memory) -> None:
    memory.save_decision("run-1", "c1", "Sarah Moutou", "Shortlist", None, "Alisha", None, None)
    memory.save_decision("run-2", "c1", "Sarah Moutou", "Hold", None, "Alisha", None, None)
    memory.reset_decisions("run-1", actor="Alisha")
    assert memory.get_decisions("run-1") == {}
    assert len(memory.get_decisions("run-2")) == 1


# --- Audit log --------------------------------------------------------------


def test_audit_log_is_written(memory) -> None:
    memory.add_preference("Prefers retail experience", actor="Alisha")
    memory.save_approved_job("JD text", make_requirements(), approved_by="Alisha")
    memory.save_decision("run-1", "c1", "Sarah Moutou", "Shortlist", None, "Alisha", None, None)
    memory.save_report_draft("run-1", "# Draft", actor="Alisha")
    memory.approve_report("run-1", "# Final", approved_by="Alisha")

    events = [row["event"] for row in memory.list_audit()]
    # Newest first.
    assert events == [
        "report_approved",
        "report_drafted",
        "decision_saved",
        "requirements_approved",
        "preference_added",
    ]
    assert all(row["actor"] == "Alisha" for row in memory.list_audit())


def test_blocked_rejection_writes_no_audit_row(memory) -> None:
    with pytest.raises(DecisionError):
        memory.save_decision("run-1", "c6", "Ryan Chen", "Reject", "", "Alisha", None, None)
    assert memory.list_audit() == []


# --- Jobs, documents, runs, reports, cache ---------------------------------------


def test_approved_job_is_saved_under_its_title(memory) -> None:
    first_id = memory.save_approved_job("JD v1", make_requirements(), approved_by="Alisha")
    second_id = memory.save_approved_job("JD v2", make_requirements(), approved_by="Alisha")
    assert first_id == second_id  # same title: updated, not duplicated
    job = memory.get_job_by_title("Marketing Executive")
    assert job["jd_text"] == "JD v2"
    assert job["requirements"].salary_max == 60000
    assert job["approved_by"] == "Alisha"


def test_duplicate_document_is_ignored(memory) -> None:
    first_id, first_is_new = memory.add_document(make_intake())
    second_id, second_is_new = memory.add_document(make_intake())
    assert first_is_new is True
    assert second_is_new is False
    assert first_id == second_id
    assert len(memory.list_documents()) == 1
    assert memory.list_documents()[0]["redactions"] == ["Date of birth"]


def test_document_can_be_removed_and_cleared(memory) -> None:
    sarah_id, _ = memory.add_document(make_intake("Sarah_Moutou_CV.pdf", "a"))
    memory.add_document(make_intake("Kevin_Ramdin_CV.docx", "b"))
    memory.remove_document(sarah_id)
    assert [d["file_name"] for d in memory.list_documents()] == ["Kevin_Ramdin_CV.docx"]
    memory.clear_working_documents()
    assert memory.list_documents() == []


def test_latest_run_is_loaded(memory) -> None:
    memory.save_run(make_run("run-1", "2026-10-08T10:00:00+00:00"))
    memory.save_run(make_run("run-2", "2026-10-08T11:00:00+00:00"))
    assert memory.load_latest_run().run_id == "run-2"
    assert memory.load_run("run-1").model == "gemini-3.8-flash"
    assert len(memory.list_runs()) == 2


def test_saving_a_run_again_overwrites_it(memory) -> None:
    run = make_run()
    memory.save_run(run)
    run.stats = {"candidates": 6}
    memory.save_run(run)
    assert memory.load_run("run-1").stats == {"candidates": 6}
    assert len(memory.list_runs()) == 1


def test_report_draft_and_approval(memory) -> None:
    memory.save_report_draft("run-1", "# Draft", actor="Alisha")
    assert memory.get_report("run-1")["approved_by"] is None
    memory.approve_report("run-1", "# Final", approved_by="Alisha")
    report = memory.get_report("run-1")
    assert report["final_md"] == "# Final"
    assert report["approved_by"] == "Alisha"


def test_cache_put_and_get(memory) -> None:
    assert memory.cache_get("key1") is None
    memory.cache_put("key1", "cv_analyst", "gemini", "gemini-3.8-flash", '{"name": "Sarah"}', "{}")
    memory.cache_put("key1", "cv_analyst", "gemini", "gemini-3.8-flash", '{"name": "Sarah M"}', "{}")
    assert memory.cache_get("key1")["response_json"] == '{"name": "Sarah M"}'


# --- Persistence (AC10) -----------------------------------------------------


def test_data_survives_reopening_the_database(database_url) -> None:
    first = Memory(database_url)
    first.add_preference("TikTok experience matters a lot", actor="Alisha")
    first.save_decision("run-1", "c1", "Sarah Moutou", "Shortlist", None, "Alisha", "Strong match", 88.6)
    first.save_run(make_run())
    first.add_document(make_intake())
    first.close()

    reopened = Memory(database_url)
    assert [p["text"] for p in reopened.list_preferences()] == ["TikTok experience matters a lot"]
    assert reopened.get_decisions("run-1")["c1"]["decision"] == "Shortlist"
    assert reopened.load_latest_run().run_id == "run-1"
    assert len(reopened.list_documents()) == 1
    assert len(reopened.list_audit()) == 2
    reopened.close()


# --- Postgres support (Phase 7) ------------------------------------------------


def test_neon_urls_use_the_psycopg_driver() -> None:
    from core.memory import to_sqlalchemy_url

    assert to_sqlalchemy_url("postgresql://u:p@host/db?sslmode=require") == "postgresql+psycopg://u:p@host/db?sslmode=require"
    assert to_sqlalchemy_url("postgres://u:p@host/db").startswith("postgresql+psycopg://")
    assert to_sqlalchemy_url("sqlite:///x.db") == "sqlite:///x.db"
    assert to_sqlalchemy_url("").startswith("sqlite:///")


def test_every_table_compiles_for_postgres() -> None:
    """The same tables must work on Neon: generate the Postgres CREATE TABLE statements without a database."""
    from sqlalchemy import create_mock_engine

    from core.memory import metadata

    statements: list[str] = []
    engine = create_mock_engine("postgresql+psycopg://", lambda sql, *args, **kwargs: statements.append(str(sql.compile(dialect=engine.dialect))))
    metadata.create_all(engine, checkfirst=False)
    created = " ".join(statements)
    for table in ["preferences", "jobs", "documents", "runs", "decisions", "reports", "audit", "llm_cache"]:
        assert f"CREATE TABLE {table}" in created
