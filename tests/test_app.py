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
    assert app.header[0].value == "1. Job setup"


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
