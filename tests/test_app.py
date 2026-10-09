"""Smoke tests for the Streamlit app: every page opens without an error, and the core flow works.

Streamlit's AppTest runs app.py without a browser. Each test uses its own
temporary database and the FakeLLM, so no real AI is ever called.
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP_TIMEOUT = 60  # seconds; the first run imports a lot
APP_FILE = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture
def app(tmp_path, monkeypatch):
    """A fresh app with an empty temporary database."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + (tmp_path / "app.db").as_posix())
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    test_app = AppTest.from_file(APP_FILE, default_timeout=APP_TIMEOUT)
    test_app.run()
    return test_app


def no_errors(test_app: AppTest) -> bool:
    return len(test_app.exception) == 0 and len(test_app.error) == 0


def go_to(test_app: AppTest, page_key: str) -> None:
    test_app.button(key=f"nav_{page_key}").click().run()


def test_app_starts_on_job_setup(app) -> None:
    assert no_errors(app)
    assert app.header[0].value == "Job setup"


@pytest.mark.parametrize("page_key", ["candidates", "screening", "review", "report", "behind"])
def test_every_page_opens_without_errors(app, page_key) -> None:
    go_to(app, page_key)
    assert no_errors(app)


def test_sample_cvs_load_with_intake_badges(app) -> None:
    go_to(app, "candidates")
    [button for button in app.button if button.label == "Use sample CVs"][0].click().run()
    assert no_errors(app)
    page_text = " ".join(block.value for block in app.markdown)
    assert "6 CV(s) loaded" in page_text
    assert "details redacted" in page_text  # Jean-Marc
    assert "Suspicious text removed" in page_text  # Ryan


def test_run_screening_is_disabled_until_ready(app) -> None:
    go_to(app, "screening")
    run_button = [button for button in app.button if button.label == "Run screening"][0]
    assert run_button.disabled
    captions = " ".join(caption.value for caption in app.caption)
    assert "Approve the requirements first" in captions


def click(test_app: AppTest, label: str) -> None:
    [button for button in test_app.button if button.label == label][0].click().run()


def all_text(test_app: AppTest) -> str:
    return " ".join(block.value for block in test_app.markdown)


def test_full_phase_3_flow_with_fake_model(tmp_path, monkeypatch) -> None:
    """Load the sample job, extract, approve, load CVs, run screening, then restart."""
    from tests.fake_llm import FakeLLM

    database_url = "sqlite:///" + (tmp_path / "flow.db").as_posix()
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr("ui.state.make_llm", lambda settings, memory: FakeLLM())

    app = AppTest.from_file(APP_FILE, default_timeout=APP_TIMEOUT)
    app.run()
    click(app, "Load the sample job (Marketing Executive)")
    click(app, "Extract requirements")
    assert no_errors(app)
    click(app, "Approve requirements")
    assert no_errors(app)
    assert "Approved by Recruiter" in all_text(app)

    go_to(app, "candidates")
    click(app, "Use sample CVs")
    go_to(app, "screening")
    click(app, "Run screening")
    assert no_errors(app)

    run = app.session_state["run"]
    assert len(run.candidates) == 6
    assert all(candidate.error is None for candidate in run.candidates)
    assert "Jean-Marc Lebrun" in all_text(app)  # the timeline shows real names

    # FR-M2: a brand-new session (like restarting the app) reloads everything.
    restarted = AppTest.from_file(APP_FILE, default_timeout=APP_TIMEOUT)
    restarted.run()
    assert "Approved by Recruiter" in all_text(restarted)
    assert len(restarted.session_state["run"].candidates) == 6


def test_preference_can_be_saved(app) -> None:
    app.text_input(key="new_preference").input("TikTok experience matters a lot").run()
    [button for button in app.button if button.label == "Save preference"][0].click().run()
    assert no_errors(app)
    assert app.text_input(key="new_preference").value == ""
    page_text = " ".join(block.value for block in app.markdown)
    assert "TikTok experience matters a lot" in page_text


def screened_app(tmp_path, monkeypatch) -> AppTest:
    """An app that has gone through Phase 3: requirements approved, sample CVs screened (fake model)."""
    from tests.fake_llm import FakeLLM

    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + (tmp_path / "review.db").as_posix())
    monkeypatch.setattr("ui.state.make_llm", lambda settings, memory: FakeLLM())
    test_app = AppTest.from_file(APP_FILE, default_timeout=APP_TIMEOUT)
    test_app.run()
    click(test_app, "Load the sample job (Marketing Executive)")
    click(test_app, "Extract requirements")
    click(test_app, "Approve requirements")
    go_to(test_app, "candidates")
    click(test_app, "Use sample CVs")
    go_to(test_app, "screening")
    click(test_app, "Run screening")
    return test_app


def candidate_id(test_app: AppTest, first_name: str) -> str:
    run = test_app.session_state["run"]
    return next(c.candidate_id for c in run.candidates if c.profile.name.startswith(first_name))


def decide(test_app: AppTest, first_name: str, decision: str, reason: str = "") -> None:
    """Open a candidate on the Review page and save a decision."""
    cid = candidate_id(test_app, first_name)
    test_app.selectbox(key="selected_candidate").set_value(cid).run()
    test_app.radio(key=f"decision_{cid}").set_value(decision).run()
    test_app.text_area(key=f"reason_{cid}").input(reason).run()
    test_app.button(key=f"save_{cid}").click().run()


def test_full_phase_4_journey_with_fake_model(tmp_path, monkeypatch) -> None:
    """Review → decisions (a rejection without a reason is blocked) → report → approve → download."""
    app = screened_app(tmp_path, monkeypatch)
    go_to(app, "review")
    assert no_errors(app)
    assert "AI recommendation, not a decision" in all_text(app)

    # A rejection without a reason is blocked (FR-D2, AC8).
    decide(app, "Ryan", "Reject", "")
    assert any("job-related reason" in error.value for error in app.error)
    from core.memory import Memory

    memory = Memory("sqlite:///" + (tmp_path / "review.db").as_posix())
    run_id = app.session_state["run"].run_id
    assert memory.get_decisions(run_id) == {}

    decide(app, "Ryan", "Reject", "No digital marketing experience")
    decide(app, "Sarah", "Shortlist", "Great portfolio")
    decide(app, "Kevin", "Shortlist")
    decide(app, "Aisha", "Hold", "Check references")
    assert no_errors(app)
    decisions = memory.get_decisions(run_id)
    assert {row["decision"] for row in decisions.values()} == {"Reject", "Shortlist", "Hold"}

    # Shortlist report: only shortlisted candidates (FR-P5).
    go_to(app, "report")
    assert no_errors(app)
    assert not app.get("download_button")  # no downloads before approval
    click(app, "Draft report")
    assert no_errors(app)
    draft = app.text_area(key="report_editor").value
    assert "## Sarah" in draft and "## Kevin" in draft
    assert "Ryan" not in draft and "Aisha" not in draft
    click(app, "Approve report")
    assert no_errors(app)
    assert "Approved by Recruiter" in all_text(app)
    assert memory.get_report(run_id)["approved_by"] == "Recruiter"
    assert len(app.get("download_button")) == 2

    # Behind the scenes shows the audit of those actions.
    go_to(app, "behind")
    assert no_errors(app)
    memory.close()


def test_screen_new_cvs_adds_nadia_to_the_ranking(tmp_path, monkeypatch) -> None:
    from core.documents import content_hash, read_document_file
    from core.guardrails import run_intake
    from core.memory import Memory
    from tests.conftest import LIVE_DEMO_DIR

    app = screened_app(tmp_path, monkeypatch)
    nadia_path = sorted(LIVE_DEMO_DIR.iterdir())[0]
    memory = Memory("sqlite:///" + (tmp_path / "review.db").as_posix())
    intake = run_intake(nadia_path.name, read_document_file(nadia_path), content_hash(nadia_path.read_bytes()))
    memory.add_document(intake, kind="cv")  # the same as uploading her CV on page 2
    memory.close()

    click(app, "Screen new CVs")
    assert no_errors(app)
    names = [c.profile.name for c in app.session_state["run"].candidates]
    assert len(names) == 7 and any(name.startswith("Nadia") for name in names)
    go_to(app, "review")
    assert no_errors(app)
    assert "Nadia" in all_text(app)


def test_reset_decisions_needs_confirmation(tmp_path, monkeypatch) -> None:
    app = screened_app(tmp_path, monkeypatch)
    go_to(app, "review")
    decide(app, "Sarah", "Shortlist")
    click(app, "Reset decisions")
    click(app, "Yes, reset")
    assert no_errors(app)
    assert "0 of 6 candidates decided" in all_text(app) or "Decision: Pending" in all_text(app)


def test_access_code_is_required_when_set(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + (tmp_path / "gate.db").as_posix())
    monkeypatch.setenv("APP_ACCESS_CODE", "demo-code")
    app = AppTest.from_file(APP_FILE, default_timeout=APP_TIMEOUT)
    app.run()
    assert len(app.header) == 0  # nothing shown before the code
    app.text_input(key="access_code_input").input("wrong").run()
    click(app, "Enter")
    assert any("isn't right" in error.value for error in app.error)
    app.text_input(key="access_code_input").input("demo-code").run()
    click(app, "Enter")
    assert no_errors(app)
    assert app.header[0].value == "Job setup"


def test_deployed_app_hides_ollama(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + (tmp_path / "cloud.db").as_posix())
    monkeypatch.setenv("DEPLOYED", "true")
    app = AppTest.from_file(APP_FILE, default_timeout=APP_TIMEOUT)
    app.run()
    assert list(app.radio(key="provider").options) == ["Gemini"]  # Ollama is hidden


def test_extras_work_in_the_app(tmp_path, monkeypatch) -> None:
    """Excel download, Kevin's email draft, and Ask the CVs, with the fake model."""
    app = screened_app(tmp_path, monkeypatch)
    go_to(app, "review")
    assert any("Download Excel" in button.label for button in app.get("download_button"))
    cid = candidate_id(app, "Kevin")
    app.selectbox(key="selected_candidate").set_value(cid).run()
    app.button(key=f"draft_email_{cid}").click().run()
    assert no_errors(app)
    assert "salary expectation" in app.text_area(key=f"email_body_{cid}").value
    app.button(key=f"approve_email_{cid}").click().run()
    assert no_errors(app)

    go_to(app, "ask")
    assert no_errors(app)
    click(app, "Who has TikTok experience?")
    assert no_errors(app)
    assert "Quote verified" in all_text(app)
