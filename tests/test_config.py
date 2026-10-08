"""Tests for core/config.py: settings are read correctly and secrets stay hidden.

These tests never read your real .env file and never call any API.
"""

from core.config import build_settings


def test_defaults_are_used_when_nothing_is_set() -> None:
    settings = build_settings({})
    assert settings.llm_provider == "gemini"
    assert settings.gemini_model == "gemini-3.8-flash"
    assert settings.gemini_fallback_models == ["gemini-3.7-flash", "gemini-3.6-flash"]
    assert settings.ollama_model == "qwen2.5:7b"
    assert settings.min_seconds_between_calls == 4.0
    assert settings.use_cache is True
    assert settings.deployed is False
    assert settings.database_url == ""
    assert settings.has_gemini_key is False


def test_gemini_models_in_order_puts_main_model_first_without_duplicates() -> None:
    settings = build_settings(
        {"GEMINI_MODEL": "model-a", "GEMINI_FALLBACK_MODELS": "model-b, model-a ,model-c"}
    )
    assert settings.gemini_models_in_order == ["model-a", "model-b", "model-c"]


def test_text_values_are_converted_to_the_right_types() -> None:
    settings = build_settings(
        {"USE_CACHE": "false", "DEPLOYED": "TRUE", "MIN_SECONDS_BETWEEN_CALLS": "not a number"}
    )
    assert settings.use_cache is False
    assert settings.deployed is True
    assert settings.min_seconds_between_calls == 4.0  # falls back to the default


def test_secrets_are_not_shown_when_settings_are_printed() -> None:
    settings = build_settings(
        {"GEMINI_API_KEY": "fake-key-123", "DATABASE_URL": "postgresql://user:pw@host/db"}
    )
    printed = repr(settings)
    assert "fake-key-123" not in printed
    assert "pw@host" not in printed
    assert settings.has_gemini_key is True
