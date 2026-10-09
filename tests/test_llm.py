"""Tests for core/llm.py: JSON extraction, repairs, retries, fallback and the cache.

A fake "send" function stands in for Gemini, so no real API is ever called.
"""

import pytest

from core.config import build_settings
from core.llm import (
    LLMClient,
    LLMError,
    ModelUnavailableError,
    RawReply,
    RetryableError,
    extract_json_text,
    send_to_gemini,
)
from core.memory import Memory
from core.schemas import InterviewQuestion

GOOD_REPLY = '{"question": "Tell me about a campaign.", "purpose": "M2"}'


class ScriptedSender:
    """Replies with a scripted list of answers (text, or an exception to raise)."""

    def __init__(self, script):
        self.script = list(script)
        self.models_called: list[str] = []
        self.prompts: list[str] = []

    def __call__(self, provider, model, system, prompt, schema, temperature):
        self.models_called.append(model)
        self.prompts.append(prompt)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return RawReply(item, tokens_in=10, tokens_out=5)


def make_client(sender, memory=None, use_cache=True) -> LLMClient:
    settings = build_settings({"GEMINI_MODEL": "model-a", "GEMINI_FALLBACK_MODELS": "model-b"})
    return LLMClient(
        settings=settings,
        provider="gemini",
        model="model-a",
        memory=memory,
        use_cache=use_cache,
        send_function=sender,
        sleep_function=lambda seconds: None,  # don't really wait in tests
        min_seconds_between_calls=0,
    )


def ask(client: LLMClient):
    return client.generate_json("report_candidate", "system", "prompt", InterviewQuestion, 0.1, "test@v1")


# --- JSON extraction --------------------------------------------------------


def test_extracts_json_from_code_fence() -> None:
    reply = 'Here you go:\n```json\n{"a": 1}\n```\nHope that helps!'
    assert extract_json_text(reply) == '{"a": 1}'


def test_extracts_json_from_chatty_reply() -> None:
    reply = 'Sure! The answer is {"a": {"b": 2}} as requested.'
    assert extract_json_text(reply) == '{"a": {"b": 2}}'


def test_reply_without_json_raises() -> None:
    with pytest.raises(ValueError):
        extract_json_text("I can't help with that.")


# --- Validation and repair --------------------------------------------------


def test_valid_reply_needs_no_repair() -> None:
    result, meta = ask(make_client(ScriptedSender([GOOD_REPLY])))
    assert result.purpose == "M2"
    assert meta.repairs == 0
    assert meta.calls == 1
    assert meta.prompt_version == "test@v1"


def test_invalid_then_valid_succeeds_with_one_repair() -> None:
    sender = ScriptedSender(['{"question": "Missing the purpose field"}', GOOD_REPLY])
    result, meta = ask(make_client(sender))
    assert result.question == "Tell me about a campaign."
    assert meta.repairs == 1
    assert meta.calls == 2
    assert "<previous_answer>" in sender.prompts[1]  # the repair request shows the bad answer


def test_always_invalid_gives_a_clear_error() -> None:
    sender = ScriptedSender(["not json", "still not json", "nope"])
    with pytest.raises(LLMError, match="2 repair attempts"):
        ask(make_client(sender))
    assert len(sender.prompts) == 3  # 1 call + 2 repairs


# --- Retries and fallback ---------------------------------------------------


def test_rate_limit_is_retried() -> None:
    sender = ScriptedSender([RetryableError("429", retry_after=1), GOOD_REPLY])
    result, meta = ask(make_client(sender))
    assert meta.retries == 1
    assert meta.fallback_used is False
    assert sender.models_called == ["model-a", "model-a"]


def test_falls_back_to_next_model_after_repeated_rate_limits() -> None:
    busy = [RetryableError("429") for _ in range(6)]  # 1 try + 5 retries on model-a
    sender = ScriptedSender(busy + [GOOD_REPLY])
    result, meta = ask(make_client(sender))
    assert meta.fallback_used is True
    assert meta.model == "model-b"
    assert sender.models_called[-1] == "model-b"


def test_unknown_model_falls_back_immediately() -> None:
    sender = ScriptedSender([ModelUnavailableError("404"), GOOD_REPLY])
    result, meta = ask(make_client(sender))
    assert meta.model == "model-b"
    assert meta.retries == 0


def test_all_models_busy_gives_a_friendly_error() -> None:
    sender = ScriptedSender([RetryableError("503") for _ in range(12)])
    with pytest.raises(LLMError, match="Wait about a minute"):
        ask(make_client(sender))


def test_daily_quota_moves_to_next_model_without_retrying() -> None:
    from core.llm import DAILY_QUOTA_MESSAGE

    sender = ScriptedSender([ModelUnavailableError(DAILY_QUOTA_MESSAGE.format(model="model-a")), GOOD_REPLY])
    result, meta = ask(make_client(sender))
    assert meta.retries == 0
    assert meta.model == "model-b"


def test_daily_quota_on_every_model_says_so() -> None:
    from core.llm import DAILY_QUOTA_MESSAGE

    sender = ScriptedSender([ModelUnavailableError(DAILY_QUOTA_MESSAGE.format(model=m)) for m in ("model-a", "model-b")])
    with pytest.raises(LLMError, match="free daily limit is used up"):
        ask(make_client(sender))
    assert len(sender.models_called) == 2  # one request per model, no wasted retries


def test_daily_quota_is_recognised_from_gemini_reply() -> None:
    from core.llm import is_daily_quota_error

    class FakeResponse:
        def __init__(self, quota_id):
            self.quota_id = quota_id

        def json(self):
            return {"error": {"details": [{"violations": [{"quotaId": self.quota_id}]}]}}

    assert is_daily_quota_error(FakeResponse("GenerateRequestsPerDayPerProjectPerModel-FreeTier"))
    assert not is_daily_quota_error(FakeResponse("GenerateRequestsPerMinutePerProjectPerModel-FreeTier"))


def test_missing_api_key_gives_a_friendly_error() -> None:
    with pytest.raises(LLMError, match="No Gemini API key"):
        send_to_gemini("", "gemini-3.8-flash", "system", "prompt", 0.1)


def test_stuck_request_gives_up_at_the_deadline(monkeypatch) -> None:
    import time

    import requests

    from core.llm import post_with_deadline

    def very_slow_post(url, **options):
        time.sleep(3)  # pretend the server hangs
        return "never used"

    monkeypatch.setattr(requests, "post", very_slow_post)
    started = time.perf_counter()
    with pytest.raises(requests.Timeout):
        post_with_deadline("https://example.invalid", 0.3)
    assert time.perf_counter() - started < 2  # we stopped waiting long before the server did


def test_calls_are_spaced_out() -> None:
    waits: list[float] = []
    sender = ScriptedSender([GOOD_REPLY, GOOD_REPLY])
    client = make_client(sender, use_cache=False)
    client.min_seconds_between_calls = 4
    client.sleep_function = waits.append
    client.clock = lambda: 100.0  # time stands still, so the full 4 seconds are needed
    ask(client)
    ask(client)
    assert waits == [4.0]


# --- Cache ------------------------------------------------------------------


@pytest.fixture
def memory(tmp_path):
    memory = Memory("sqlite:///" + (tmp_path / "cache.db").as_posix())
    yield memory
    memory.close()


def test_cache_hit_returns_an_identical_result(memory) -> None:
    sender = ScriptedSender([GOOD_REPLY])  # only ONE reply available
    client = make_client(sender, memory=memory)
    first, first_meta = ask(client)
    second, second_meta = ask(client)  # would fail if it called the sender again
    assert first == second
    assert first_meta.cache_hit is False
    assert second_meta.cache_hit is True
    assert len(sender.prompts) == 1


def test_cache_off_calls_the_model_again(memory) -> None:
    sender = ScriptedSender([GOOD_REPLY, GOOD_REPLY])
    client = make_client(sender, memory=memory, use_cache=False)
    ask(client)
    ask(client)
    assert len(sender.prompts) == 2
