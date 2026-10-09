"""TalentLens: the Streamlit app the recruiter opens in the browser.

Start it with:  streamlit run app.py

This file sets up the page and the top bar: the logo, a step indicator for the
six steps (with ✓ on finished steps), and pop-out panels for Settings (reviewer
name, model, cache, New screening) and Memory (recruiter preferences). Then it
shows the chosen page, with Back / Next buttons underneath.
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
    page_ask,
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

# The six steps of the flow, in order: (page key, label in the step indicator).
STEPS: list[tuple[str, str]] = [
    ("job_setup", "Job setup"),
    ("candidates", "Candidates"),
    ("screening", "Screening"),
    ("review", "Review"),
    ("report", "Report"),
    ("behind", "Behind the scenes"),
]
STEP_KEYS = [key for key, _label in STEPS]
CIRCLED_NUMBERS = ["①", "②", "③", "④", "⑤", "⑥"]


# ===========================================================================
# Step indicator
# ===========================================================================


def step_is_done(page_key: str, memory: Memory) -> bool:
    """Which steps get a ✓ in the step indicator."""
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


def go_to(page_key: str) -> None:
    """Button callback: open a page."""
    st.session_state["page"] = page_key


def step_styles(done_keys: list[str]) -> str:
    """CSS for the step indicator: the current step is a filled pill, finished steps are teal."""
    current = st.session_state["page"]
    rules = [
        f".st-key-nav_{key} button {{ color: var(--tl-primary-dark) !important; }}" for key in done_keys
    ]
    rules.append(
        f".st-key-tl-stepper .st-key-nav_{current} button, .st-key-tl-stepper .st-key-nav_{current} button:hover "
        f"{{ background: var(--tl-primary) !important; color: #FFFFFF !important; "
        f"box-shadow: 0 2px 8px rgba(14,124,123,0.3) !important; }}"
    )
    return "<style>" + "\n".join(rules) + "</style>"


def show_stepper(memory: Memory) -> None:
    done_keys = [key for key in STEP_KEYS if step_is_done(key, memory)]
    components.show(step_styles(done_keys))
    with st.container(key="tl-stepper"):
        columns = st.columns(len(STEPS), gap="small")
        for position, (column, (page_key, label)) in enumerate(zip(columns, STEPS)):
            marker = "✓" if page_key in done_keys else CIRCLED_NUMBERS[position]
            column.button(f"{marker} {label}", key=f"nav_{page_key}", width="stretch",
                          on_click=go_to, args=(page_key,))


# ===========================================================================
# Settings and Memory panels
# ===========================================================================


def provider_label() -> str:
    if st.session_state["provider"] == "gemini":
        return "Gemini"
    return "Ollama"


def show_model_settings(settings: Settings, memory: Memory) -> None:
    st.markdown('<p class="tl-section-title">AI model</p>', unsafe_allow_html=True)
    providers = ["gemini"] if settings.deployed else ["gemini", "ollama"]  # Ollama is local only
    st.radio("AI provider", providers, key="provider", horizontal=True,
             format_func=lambda name: "Gemini" if name == "gemini" else "Ollama (local)")
    if st.session_state["provider"] == "gemini":
        st.text_input("Gemini model", key="gemini_model", help="Options: " + ", ".join(state.GEMINI_MODEL_CHOICES))
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


def show_settings(settings: Settings, memory: Memory) -> None:
    with st.popover(f"⚙ Settings · {provider_label()}", width="stretch", help="Reviewer name, AI model, cache, new screening"):
        st.text_input("Reviewer name", key="reviewer", help="Recorded with every approval and decision.")
        st.divider()
        show_model_settings(settings, memory)
        st.divider()
        show_new_screening(memory)


def show_preferences(memory: Memory) -> None:
    with st.popover("🧠 Memory", width="stretch", help="Recruiter preferences remembered between sessions"):
        st.markdown('<p class="tl-section-title">Recruiter preferences</p>', unsafe_allow_html=True)
        st.caption("Given to the Job Analyst the next time you extract requirements.")
        preferences = memory.list_preferences()
        if not preferences:
            st.caption("No preferences saved yet.")
        for preference in preferences:
            columns = st.columns([6, 1], vertical_alignment="center")
            columns[0].markdown(f"- {preference['text']}")
            if columns[1].button("✖", key=f"delete_pref_{preference['id']}", help="Delete this preference"):
                memory.delete_preference(preference["id"], actor=state.reviewer_name())
                st.rerun()
        st.text_input("Add a preference", placeholder="e.g. TikTok experience matters a lot", key="new_preference")
        st.button("Save preference", on_click=save_preference, args=(memory,), type="primary")


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
        if st.button("New screening", width="stretch", help="Clears the job, CVs and run; keeps preferences and history"):
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


# ===========================================================================
# Top bar
# ===========================================================================


def show_topbar(settings: Settings, memory: Memory) -> None:
    """Row 1: logo and actions (Ask, Memory, Settings). Row 2: the step indicator, full width."""
    with st.container(key="tl-topbar"):
        top = st.columns([4.6, 1.45, 1.2, 1.6], vertical_alignment="center", gap="small")
        with top[0]:
            components.show(components.logo_html(APP_NAME, TAGLINE))
        with top[1], st.container(key="tl-actions-ask"):
            ask_current = st.session_state["page"] == "ask"
            st.button("💬 Ask the CVs", key="nav_ask", width="stretch", on_click=go_to, args=("ask",),
                      type="primary" if ask_current else "secondary", help="Ask questions across all CVs")
        with top[2], st.container(key="tl-actions-memory"):
            show_preferences(memory)
        with top[3], st.container(key="tl-actions-settings"):
            show_settings(settings, memory)
        show_stepper(memory)


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
    "ask": page_ask.render,
}


def show_footer() -> None:
    """Back / Next buttons under each step, so the flow guides the recruiter."""
    page = st.session_state["page"]
    if page not in STEP_KEYS:
        return
    position = STEP_KEYS.index(page)
    labels = dict(STEPS)
    with st.container(key="tl-footer"):
        columns = st.columns([1, 3, 1])
        if position > 0:
            previous = STEP_KEYS[position - 1]
            columns[0].button(f"← {labels[previous]}", key="footer_prev", width="stretch",
                              on_click=go_to, args=(previous,))
        if position < len(STEP_KEYS) - 1:
            following = STEP_KEYS[position + 1]
            columns[2].button(f"{labels[following]} →", key="footer_next", width="stretch", type="primary",
                              on_click=go_to, args=(following,))


def show_page(settings: Settings, memory: Memory) -> None:
    PAGE_RENDERERS[st.session_state["page"]](settings, memory)


def access_granted(settings: Settings) -> bool:
    """FR-X3: when APP_ACCESS_CODE is set, ask for it before showing anything.

    This stops strangers using up the free Gemini quota on the public link.
    """
    if not settings.app_access_code or st.session_state.get("access_granted"):
        return True
    left, middle, right = st.columns([1, 1.2, 1])
    with middle, st.container(key="card-access"):
        components.show(components.logo_html(APP_NAME, TAGLINE))
        st.markdown("This demo is protected. Please enter the access code.")
        code = st.text_input("Access code", type="password", key="access_code_input")
        if st.button("Enter", type="primary", width="stretch"):
            # compare_digest takes the same time whatever the input, so the code can't be guessed by timing.
            if hmac.compare_digest(code.strip(), settings.app_access_code):
                st.session_state["access_granted"] = True
                st.rerun()
            st.error("That code isn't right. Please check it and try again.")
    return False


def main() -> None:
    st.set_page_config(page_title=APP_NAME, page_icon="🔍", layout="wide", initial_sidebar_state="collapsed")
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
    show_topbar(settings, memory)
    try:
        show_page(settings, memory)
    except Exception as error:  # a last safety net: never show a raw Python error
        st.error(state.friendly_error(error))
    show_footer()


main()
