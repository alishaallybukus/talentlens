"""Reads TalentLens settings and gives the rest of the app one tidy Settings object.

Why this file exists:
- On your laptop, settings (like the Gemini API key) live in the `.env` file.
- On Streamlit Cloud there is no `.env`; settings live in "Streamlit secrets".
This file checks both places, so no other file needs to care where a setting came from.

Secrets (the API key, DATABASE_URL) are never printed or logged by this file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# The project folder is the parent of the `core` folder this file sits in.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
ENV_FILE: Path = PROJECT_ROOT / ".env"
DEFAULT_SQLITE_PATH: Path = PROJECT_ROOT / "data" / "talentlens.db"

# Every setting the app knows about, with its default value (as text).
# The defaults match `.env.example`.
DEFAULTS: dict[str, str] = {
    "LLM_PROVIDER": "gemini",
    "GEMINI_API_KEY": "",
    "GEMINI_MODEL": "gemini-3.8-flash",
    "GEMINI_FALLBACK_MODELS": "gemini-3.7-flash,gemini-3.6-flash",
    "OLLAMA_URL": "http://localhost:11434",
    "OLLAMA_MODEL": "qwen2.5:7b",
    "MIN_SECONDS_BETWEEN_CALLS": "4",
    "USE_CACHE": "true",
    "REVIEWER_NAME": "Recruiter",
    "DATABASE_URL": "",
    "DEPLOYED": "false",
    "N8N_WEBHOOK_URL": "",
    "APP_ACCESS_CODE": "",
}


@dataclass(frozen=True)
class Settings:
    """All app settings, already converted to the right types."""

    llm_provider: str
    gemini_api_key: str = field(repr=False)  # repr=False: never shown when printed
    gemini_model: str
    gemini_fallback_models: list[str]
    ollama_url: str
    ollama_model: str
    min_seconds_between_calls: float
    use_cache: bool
    reviewer_name: str
    database_url: str = field(repr=False)  # may contain a password
    deployed: bool
    n8n_webhook_url: str
    app_access_code: str = field(repr=False)

    @property
    def gemini_models_in_order(self) -> list[str]:
        """The main Gemini model first, then the fallbacks, without duplicates."""
        ordered: list[str] = []
        for model_name in [self.gemini_model, *self.gemini_fallback_models]:
            if model_name and model_name not in ordered:
                ordered.append(model_name)
        return ordered

    @property
    def has_gemini_key(self) -> bool:
        """True when a Gemini key is set (we never reveal the key itself)."""
        return self.gemini_api_key != ""


def text_to_bool(text: str) -> bool:
    """Turn text such as 'true', 'Yes' or '1' into True; anything else is False."""
    return text.strip().lower() in {"true", "yes", "1", "on"}


def text_to_list(text: str) -> list[str]:
    """Turn 'a, b,c' into ['a', 'b', 'c'], skipping empty items."""
    items: list[str] = []
    for part in text.split(","):
        cleaned = part.strip()
        if cleaned:
            items.append(cleaned)
    return items


def text_to_float(text: str, default: float) -> float:
    """Turn '4' into 4.0. If the text isn't a number, use the default."""
    try:
        return float(text)
    except ValueError:
        return default


def build_settings(raw_values: dict[str, str]) -> Settings:
    """Build a Settings object from plain text values.

    Any setting missing from `raw_values` (or left blank) uses its default.
    This function doesn't read files, so tests can call it directly.
    """
    values: dict[str, str] = {}
    for name, default in DEFAULTS.items():
        given = raw_values.get(name, "").strip()
        # An empty value means "use the default" (for the key, the default is empty anyway).
        values[name] = given if given else default

    return Settings(
        llm_provider=values["LLM_PROVIDER"].lower(),
        gemini_api_key=values["GEMINI_API_KEY"],
        gemini_model=values["GEMINI_MODEL"],
        gemini_fallback_models=text_to_list(values["GEMINI_FALLBACK_MODELS"]),
        ollama_url=values["OLLAMA_URL"].rstrip("/"),
        ollama_model=values["OLLAMA_MODEL"],
        min_seconds_between_calls=text_to_float(values["MIN_SECONDS_BETWEEN_CALLS"], 4.0),
        use_cache=text_to_bool(values["USE_CACHE"]),
        reviewer_name=values["REVIEWER_NAME"],
        database_url=values["DATABASE_URL"],
        deployed=text_to_bool(values["DEPLOYED"]),
        n8n_webhook_url=values["N8N_WEBHOOK_URL"],
        app_access_code=values["APP_ACCESS_CODE"],
    )


def read_streamlit_secrets() -> dict[str, str]:
    """Read settings from Streamlit secrets, if there are any.

    Locally there is usually no secrets file. Streamlit then raises an error
    when we look, so we treat any problem as "no secrets" and carry on.
    """
    try:
        import streamlit as st

        secrets: dict[str, str] = {}
        for name in DEFAULTS:
            if name in st.secrets:
                secrets[name] = str(st.secrets[name])
        return secrets
    except Exception:
        return {}


def read_environment() -> dict[str, str]:
    """Read settings from the `.env` file and the system environment."""
    # load_dotenv copies values from .env into the environment.
    # override=False means a real environment variable wins over the file.
    load_dotenv(ENV_FILE, encoding="utf-8", override=False)
    found: dict[str, str] = {}
    for name in DEFAULTS:
        value = os.environ.get(name)
        if value is not None:
            found[name] = value
    return found


def get_settings() -> Settings:
    """Collect settings from every source and return them as one Settings object.

    Order of priority (highest first): Streamlit secrets, then `.env` / environment,
    then the defaults above.
    """
    raw_values = read_environment()
    raw_values.update(read_streamlit_secrets())
    return build_settings(raw_values)
