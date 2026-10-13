"""The six steps of the TalentLens flow, which ones are finished, and moving between pages.

Why this file exists: the top bar (step indicator), the Back / Next buttons and
the Home dashboard all need the same list of steps and the same idea of
"finished", so it lives in one place.
"""

from __future__ import annotations

import streamlit as st

from core.memory import Memory
from core.report import count_decisions
from ui import state

# (page key, label), in order.
STEPS: list[tuple[str, str]] = [
    ("job_setup", "Job setup"),
    ("candidates", "Candidates"),
    ("screening", "Screening"),
    ("review", "Review"),
    ("report", "Report"),
    ("behind", "Behind the scenes"),
]
STEP_KEYS: list[str] = [key for key, _label in STEPS]
STEP_LABELS: dict[str, str] = dict(STEPS)


def go_to(page_key: str) -> None:
    """Button callback: open a page."""
    st.session_state["page"] = page_key


def step_is_done(page_key: str, memory: Memory) -> bool:
    """Which steps count as finished (they get a ✓)."""
    run = state.current_run()
    if page_key == "job_setup":
        return state.approved_requirements() is not None
    if page_key == "candidates":
        return len(memory.list_documents(kind="cv")) > 0
    if page_key == "screening":
        return run is not None and any(c.error is None for c in run.candidates)
    if run is None:
        return False
    if page_key == "review":  # every screened candidate has a decision
        counts = count_decisions(run, memory.get_decisions(run.run_id))
        return sum(counts.values()) > 0 and counts["Pending"] == 0
    if page_key == "report":
        report = memory.get_report(run.run_id)
        return report is not None and report["approved_at"] is not None
    return False


def done_steps(memory: Memory) -> list[str]:
    return [key for key in STEP_KEYS if step_is_done(key, memory)]


def next_step(memory: Memory) -> str:
    """The first step that isn't finished yet (the last step if everything is done)."""
    done = done_steps(memory)
    for key in STEP_KEYS[:-1]:  # "Behind the scenes" is never really "to do"
        if key not in done:
            return key
    return STEP_KEYS[-1]
