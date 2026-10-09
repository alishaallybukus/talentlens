"""The app's working memory while it runs (Streamlit "session state"), and reloading it on start-up.

Why this file exists:
- Streamlit re-runs the whole script after every click. Anything we want to keep
  between clicks (the job, the requirements, the current run) lives in
  st.session_state. This file is the one place that sets it up.
- On start-up it reloads the latest approved job, the guidelines, the loaded CVs
  and the latest run from the database (FR-M2), so closing the app loses nothing.
- It also builds the LLM client from the sidebar's model settings.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from core.agents import AgentError
from core.config import PROJECT_ROOT, Settings
from core.documents import DocumentReadError, content_hash
from core.llm import LLMClient, LLMError
from core.memory import DecisionError, Memory
from core.rag import GuidelineIndex, build_index
from core.schemas import IntakeResult, JobRequirements, RunResult

SAMPLE_DIR: Path = PROJECT_ROOT / "data" / "sample"
SAMPLE_JOB_FILE = SAMPLE_DIR / "job_description.md"
SAMPLE_GUIDELINES_FILE = SAMPLE_DIR / "hiring_guidelines.md"
SAMPLE_CV_DIR = SAMPLE_DIR / "cvs"

GEMINI_MODEL_CHOICES = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash"]


@st.cache_resource
def get_memory(database_url: str) -> Memory:
    """One database connection for the whole app (shared across clicks and sessions)."""
    return Memory(database_url)


@st.cache_resource
def get_index(guidelines_text: str) -> GuidelineIndex:
    """The guideline search index, rebuilt only when the guidelines text changes."""
    return build_index(guidelines_text)


# ===========================================================================
# Session set-up and restore
# ===========================================================================


def init_session(settings: Settings, memory: Memory) -> None:
    """Fill session state with defaults, then reload the working area once per browser session."""
    defaults = {
        "page": "job_setup",
        "reviewer": settings.reviewer_name,
        "provider": settings.llm_provider if not settings.deployed else "gemini",
        "gemini_model": settings.gemini_model,
        "ollama_model": settings.ollama_model,
        "use_cache": settings.use_cache,
        "jd_text": "",
        "jd_source": "",
        "guidelines_text": "",
        "guidelines_source": "",
        "draft_requirements": None,  # JobRequirements from the Job Analyst, not yet approved
        "approved_requirements": None,  # JobRequirements after Checkpoint 1
        "approved_by": None,
        "approved_at": None,
        "job_id": None,
        "run": None,  # the current RunResult
        "uploader_key": 0,
        "restored": False,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)

    if not st.session_state["restored"]:
        restore_working_area(memory)
        st.session_state["restored"] = True


def is_after(timestamp: str | None, cutoff: str | None) -> bool:
    """True if `timestamp` is after the last "New screening" (or there never was one)."""
    if timestamp is None:
        return False
    return cutoff is None or timestamp > cutoff  # ISO times in UTC compare correctly as text


def restore_working_area(memory: Memory) -> None:
    """Reload the latest job, guidelines and run from the database (FR-M2)."""
    cutoff = memory.latest_event_time("new_screening")

    job = memory.get_latest_job()
    if job is not None and is_after(job["approved_at"], cutoff):
        st.session_state["jd_text"] = job["jd_text"]
        st.session_state["jd_source"] = "Saved job"
        st.session_state["approved_requirements"] = job["requirements"]
        st.session_state["approved_by"] = job["approved_by"]
        st.session_state["approved_at"] = job["approved_at"]
        st.session_state["job_id"] = job["id"]

    saved_guidelines = memory.list_documents(kind="guidelines")
    if saved_guidelines:
        latest = saved_guidelines[-1]
        st.session_state["guidelines_text"] = latest["clean_text"]
        st.session_state["guidelines_source"] = latest["file_name"]
    else:
        st.session_state["guidelines_text"] = SAMPLE_GUIDELINES_FILE.read_text(encoding="utf-8")
        st.session_state["guidelines_source"] = "Sample hiring guidelines"

    run = memory.load_latest_run()
    if run is not None and is_after(run.created_at, cutoff):
        st.session_state["run"] = run


def save_guidelines(memory: Memory, file_name: str, text: str) -> None:
    """Make these the current guidelines and keep them in the database."""
    memory.clear_working_documents(kind="guidelines")
    record = IntakeResult(
        file_name=file_name,
        content_hash=content_hash(text.encode("utf-8")),
        clean_text=text,
        word_count=len(text.split()),
    )
    memory.add_document(record, kind="guidelines")
    st.session_state["guidelines_text"] = text
    st.session_state["guidelines_source"] = file_name


def reset_session_for_new_screening() -> None:
    """Clear the working area in this browser session (preferences stay in the database)."""
    for key in ["jd_text", "jd_source", "draft_requirements", "approved_requirements",
                "approved_by", "approved_at", "job_id", "run"]:
        st.session_state[key] = None if key not in ("jd_text", "jd_source") else ""
    st.session_state["page"] = "job_setup"


# ===========================================================================
# Shortcuts used by the pages
# ===========================================================================


def current_index() -> GuidelineIndex:
    return get_index(st.session_state["guidelines_text"])


def current_run() -> RunResult | None:
    return st.session_state.get("run")


def approved_requirements() -> JobRequirements | None:
    return st.session_state.get("approved_requirements")


def reviewer_name() -> str:
    name = (st.session_state.get("reviewer") or "").strip()
    return name or "Recruiter"


def make_llm(settings: Settings, memory: Memory) -> LLMClient:
    """An LLM client using the provider, model and cache switch from the sidebar."""
    provider = st.session_state["provider"]
    model = st.session_state["gemini_model"] if provider == "gemini" else st.session_state["ollama_model"]
    return LLMClient.from_settings(
        settings, memory, provider=provider, model=model.strip(), use_cache=st.session_state["use_cache"]
    )


def active_cvs(memory: Memory) -> list[IntakeResult]:
    """The CVs in the working area, as IntakeResults for the Supervisor."""
    documents = []
    for row in memory.list_documents(kind="cv"):
        documents.append(
            IntakeResult(
                file_name=row["file_name"],
                content_hash=row["content_hash"],
                clean_text=row["clean_text"],
                word_count=row["word_count"],
                redactions=row["redactions"],
                quarantined_text=row["quarantined_text"],
            )
        )
    return documents


def friendly_error(error: Exception) -> str:
    """A message for the recruiter. Our own errors are already friendly; others never show Python details."""
    # Not plain ValueError: Pydantic's validation errors are ValueErrors full of Python detail.
    if isinstance(error, (LLMError, AgentError, DocumentReadError, DecisionError)):
        return str(error)
    return f"Something unexpected went wrong ({type(error).__name__}). Please try again."


def short_time(iso_text: str | None) -> str:
    """'2026-10-09T06:42:10+00:00' -> '09 Oct 10:42' in Mauritius time (UTC+4)."""
    if not iso_text:
        return ""
    from datetime import datetime, timedelta, timezone

    try:
        moment = datetime.fromisoformat(iso_text)
    except ValueError:
        return iso_text
    mauritius = timezone(timedelta(hours=4))
    return moment.astimezone(mauritius).strftime("%d %b %H:%M")


def today_text() -> str:
    """Today's date in Mauritius time, e.g. '09 Oct 2026' (used in the report footer)."""
    from datetime import datetime, timedelta, timezone

    mauritius = timezone(timedelta(hours=4))
    return datetime.now(mauritius).strftime("%d %b %Y")
