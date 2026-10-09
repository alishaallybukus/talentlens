"""TalentLens: the Streamlit app the recruiter opens in the browser.

Start it with:  streamlit run app.py

This file sets up the page, the sidebar (logo, step navigation, model settings,
reviewer name, recruiter preferences, New screening) and shows the chosen page.
Each page lives in its own file in the ui/ folder.
"""

from __future__ import annotations

import hmac

import streamlit as st

from core.config import Settings, get_settings
from core.memory import Memory
from core.report import count_decisions
from ui import (
    components,
    page_behind_the_scenes,
    page_candidates,
    page_job_setup,
    page_report,
    page_review,
    page_screening,
    state,
)

APP_NAME = "TalentLens"
TAGLINE = "AI does the reading. You make the call."

# (page key, label in the sidebar)
PAGES: list[tuple[str, str]] = [
    ("job_setup", "1 Job setup"),
    ("candidates", "2 Candidates"),
    ("screening", "3 Screening"),
    ("review", "4 Review"),
    ("report", "5 Shortlist report"),
    ("behind", "6 Behind the scenes"),
]


# ===========================================================================
# Sidebar
# ===========================================================================


def step_is_done(page_key: str, memory: Memory) -> bool:
    """Which steps get a ✓ in the navigation."""
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


def show_navigation(memory: Memory) -> None:
    for page_key, label in PAGES:
        tick = " ✓" if step_is_done(page_key, memory) else ""
        is_current = st.session_state["page"] == page_key
        if st.button(label + tick, key=f"nav_{page_key}", width="stretch", type="primary" if is_current else "secondary"):
            st.session_state["page"] = page_key
            st.rerun()


def show_model_settings(settings: Settings, memory: Memory) -> None:
    with st.expander("Model settings"):
        providers = ["gemini"] if settings.deployed else ["gemini", "ollama"]  # Ollama is local only
        st.radio("AI provider", providers, key="provider", horizontal=True,
                 format_func=lambda name: "Gemini" if name == "gemini" else "Ollama (local)")
        if st.session_state["provider"] == "gemini":
            st.text_input("Gemini model", key="gemini_model",
                          help="Options: " + ", ".join(state.GEMINI_MODEL_CHOICES))
        else:
            st.text_input("Ollama model", key="ollama_model")
        st.toggle("Use saved AI answers (cache)", key="use_cache",
                  help="Re-uses earlier answers for identical requests: faster, free, and the same result every time.")
        if st.button("Test connection", width="stretch"):
            with st.spinner("Asking the model..."):
                worked, message = state.make_llm(settings, memory).test_connection()
            if worked:
                st.success(message)
            else:
                st.error(message)


def show_preferences(memory: Memory) -> None:
    with st.expander("Recruiter preferences (memory)"):
        st.caption("Saved preferences are given to the Job Analyst the next time you extract requirements.")
        for preference in memory.list_preferences():
            columns = st.columns([5, 1])
            columns[0].markdown(f"- {preference['text']}")
            if columns[1].button("✖", key=f"delete_pref_{preference['id']}", help="Delete this preference"):
                memory.delete_preference(preference["id"], actor=state.reviewer_name())
                st.rerun()
        st.text_input("Add a preference", placeholder="e.g. TikTok experience matters a lot", key="new_preference")
        st.button("Save preference", on_click=save_preference, args=(memory,))


def save_preference(memory: Memory) -> None:
    """Button callback: save the typed preference, then empty the text box.

    It runs before the page is redrawn, which is the only time Streamlit allows
    a text box's value to be changed from code.
    """
    text = st.session_state.get("new_preference", "").strip()
    if not text:
        return
    memory.add_preference(text, actor=state.reviewer_name())
    st.session_state["new_preference"] = ""
    st.toast("Preference saved.")


def show_new_screening(memory: Memory) -> None:
    """FR-M5: clear the working area, keeping preferences and history. Asks for confirmation."""
    if not st.session_state.get("confirm_new_screening"):
        if st.button("New screening", width="stretch"):
            st.session_state["confirm_new_screening"] = True
            st.rerun()
        return
    st.warning("Start a new screening? The current job, CVs and run are put away (they stay in the history).")
    columns = st.columns(2)
    if columns[0].button("Yes, start new"):
        memory.start_new_screening(actor=state.reviewer_name())
        state.reset_session_for_new_screening()
        st.session_state["confirm_new_screening"] = False
        st.toast("New screening started.")
        st.rerun()
    if columns[1].button("Cancel"):
        st.session_state["confirm_new_screening"] = False
        st.rerun()


def show_sidebar(settings: Settings, memory: Memory) -> None:
    with st.sidebar:
        components.show(components.logo_html(APP_NAME, TAGLINE))
        show_navigation(memory)
        st.divider()
        st.text_input("Reviewer name", key="reviewer", help="Recorded with every approval and decision.")
        show_model_settings(settings, memory)
        show_preferences(memory)
        st.divider()
        show_new_screening(memory)


# ===========================================================================
# Main area
# ===========================================================================


PAGE_RENDERERS = {
    "job_setup": page_job_setup.render,
    "candidates": page_candidates.render,
    "screening": page_screening.render,
    "review": page_review.render,
    "report": page_report.render,
    "behind": page_behind_the_scenes.render,
}


def show_page(settings: Settings, memory: Memory) -> None:
    PAGE_RENDERERS[st.session_state["page"]](settings, memory)


def access_granted(settings: Settings) -> bool:
    """FR-X3: when APP_ACCESS_CODE is set, ask for it before showing anything.

    This stops strangers using up the free Gemini quota on the public link.
    """
    if not settings.app_access_code or st.session_state.get("access_granted"):
        return True
    components.show(components.logo_html(APP_NAME, TAGLINE))
    st.markdown("This demo is protected. Please enter the access code.")
    code = st.text_input("Access code", type="password", key="access_code_input")
    if st.button("Enter", type="primary"):
        # compare_digest takes the same time whatever the input, so the code can't be guessed by timing.
        if hmac.compare_digest(code.strip(), settings.app_access_code):
            st.session_state["access_granted"] = True
            st.rerun()
        st.error("That code isn't right. Please check it and try again.")
    return False


def main() -> None:
    st.set_page_config(page_title=APP_NAME, page_icon="🔍", layout="wide")
    components.load_styles()
    settings = get_settings()
    if not access_granted(settings):
        return
    try:
        memory = state.get_memory(settings.database_url)
        state.init_session(settings, memory)
    except Exception as error:  # e.g. the database can't be reached
        st.error(f"TalentLens couldn't open its database. {state.friendly_error(error)}")
        return
    show_sidebar(settings, memory)
    try:
        show_page(settings, memory)
    except Exception as error:  # a last safety net: never show a raw Python error
        st.error(state.friendly_error(error))


main()
