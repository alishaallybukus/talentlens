"""Error tests (Phase 6, FR-L5, AC12): every failure shows a friendly message, never a raw Python error.

The network is simulated: requests.post is replaced, so no real API is called.
"Ollama stopped" uses a local port where nothing is listening.
"""

from pathlib import Path

import pytest
import requests
from streamlit.testing.v1 import AppTest

from core.config import build_settings
from core.documents import DocumentReadError, read_document
from core.llm import LLMClient, LLMError, send_to_gemini, send_to_ollama
from core.schemas import InterviewQuestion

APP_FILE = str(Path(__file__).resolve().parent.parent / "app.py")
STOPPED_OLLAMA_URL = "http://127.0.0.1:9"  # nothing listens here
PYTHON_WORDS = ["Traceback", "Exception", "Error(", "HTTPConnectionPool", "requests."]


class FakeResponse:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self.body = body
        self.headers: dict = {}

    def json(self) -> dict:
        return self.body


def is_friendly(message: str) -> bool:
    return not any(word in message for word in PYTHON_WORDS)


def gemini_client() -> LLMClient:
    settings = build_settings({"GEMINI_API_KEY": "test-key-123", "GEMINI_MODEL": "model-a",
                               "GEMINI_FALLBACK_MODELS": "model-b"})
    return LLMClient(settings=settings, provider="gemini", model="model-a", use_cache=False,
                     sleep_function=lambda seconds: None, min_seconds_between_calls=0)


def ask(client: LLMClient):
    return client.generate_json("report_candidate", "system", "prompt", InterviewQuestion, 0.1, "test@v1")


# --- Gemini -------------------------------------------------------------------


def test_wrong_api_key(monkeypatch) -> None:
    reply = FakeResponse(400, {"error": {"message": "API key not valid. Please pass a valid API key."}})
    monkeypatch.setattr("core.llm.requests.post", lambda *args, **kwargs: reply)
    with pytest.raises(LLMError) as raised:
        send_to_gemini("wrong-key", "gemini-3.8-flash", "system", "prompt", 0.1)
    assert "Gemini refused the API key" in str(raised.value)
    assert "wrong-key" not in str(raised.value)  # never echo a key


def test_simulated_rate_limit_on_every_model(monkeypatch) -> None:
    reply = FakeResponse(429, {"error": {"message": "Resource has been exhausted (requests per minute)."}})
    monkeypatch.setattr("core.llm.requests.post", lambda *args, **kwargs: reply)
    with pytest.raises(LLMError) as raised:
        ask(gemini_client())
    message = str(raised.value)
    assert "busy or rate-limited" in message and "Wait about a minute" in message
    assert is_friendly(message)


def test_no_internet(monkeypatch) -> None:
    def offline(*args, **kwargs):
        raise requests.ConnectionError("Max retries exceeded with url")

    monkeypatch.setattr("core.llm.requests.post", offline)
    with pytest.raises(LLMError) as raised:
        ask(gemini_client())
    message = str(raised.value)
    assert "Check your internet connection" in message
    assert is_friendly(message) and "Max retries" not in message


# --- Ollama -------------------------------------------------------------------


def test_ollama_stopped() -> None:
    with pytest.raises(LLMError) as raised:
        send_to_ollama(STOPPED_OLLAMA_URL, "qwen2.5:7b", "system", "prompt", {}, 0.1)
    assert "Ollama isn't running" in str(raised.value)
    assert is_friendly(str(raised.value))


def test_ollama_model_not_downloaded(monkeypatch) -> None:
    monkeypatch.setattr("core.llm.requests.post", lambda *args, **kwargs: FakeResponse(404, {}))
    with pytest.raises(LLMError, match="ollama pull"):
        send_to_ollama("http://localhost:11434", "missing-model", "system", "prompt", {}, 0.1)


# --- Documents ----------------------------------------------------------------


def test_empty_pdf() -> None:
    with pytest.raises(DocumentReadError) as raised:
        read_document("empty.pdf", b"")
    assert is_friendly(str(raised.value))


# --- In the app: friendly messages on screen, no exceptions --------------------


@pytest.fixture
def app_with_stopped_ollama(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + (tmp_path / "errors.db").as_posix())
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", STOPPED_OLLAMA_URL)
    test_app = AppTest.from_file(APP_FILE, default_timeout=60)
    test_app.run()
    return test_app


def click(test_app: AppTest, label: str) -> None:
    [button for button in test_app.button if button.label == label][0].click().run()


def test_app_test_connection_with_ollama_stopped(app_with_stopped_ollama) -> None:
    app = app_with_stopped_ollama
    click(app, "Test connection")
    assert len(app.exception) == 0
    messages = [error.value for error in app.error]
    assert any("Ollama isn't running" in message for message in messages)


def test_app_extract_requirements_with_ollama_stopped(app_with_stopped_ollama) -> None:
    app = app_with_stopped_ollama
    click(app, "Load the sample job (Marketing Executive)")
    click(app, "Extract requirements")
    assert len(app.exception) == 0
    messages = [error.value for error in app.error]
    assert any("Ollama isn't running" in message for message in messages)
    assert all(is_friendly(message) for message in messages)


def test_app_upload_of_empty_pdf_is_refused_kindly(tmp_path, monkeypatch) -> None:
    """The Candidates page's add_cv() returns a message instead of raising."""
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + (tmp_path / "upload.db").as_posix())
    from core.memory import Memory
    from ui.page_candidates import add_cv

    memory = Memory("sqlite:///" + (tmp_path / "upload.db").as_posix())
    added, message = add_cv(memory, "scan.pdf", b"")
    assert not added
    assert message.startswith("scan.pdf:") and is_friendly(message)
    memory.close()
