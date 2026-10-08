"""TalentLens: the Streamlit app that the recruiter opens in the browser.

Start it with:  streamlit run app.py

Phase 0 version: only the page title, the tagline and a sidebar placeholder.
The real pages (job setup, candidates, screening and so on) arrive in Phases 3 and 4.
"""

import streamlit as st

from core.config import get_settings

APP_NAME = "TalentLens"
TAGLINE = "AI does the reading. You make the call."


def show_sidebar() -> None:
    """Show the sidebar. For now it's just the name, tagline and a placeholder."""
    with st.sidebar:
        st.title(APP_NAME)
        st.caption(TAGLINE)
        st.divider()
        st.info("Step navigation and model settings will appear here in Phase 3.")


def show_main_page() -> None:
    """Show the main area of the page."""
    st.title(APP_NAME)
    st.subheader(TAGLINE)
    st.write(
        "An AI recruitment assistant: a team of AI agents reads and compares CVs "
        "against the job's requirements and the company's hiring guidelines. "
        "The recruiter makes every decision."
    )

    # A friendly reminder if the Gemini key hasn't been added yet.
    # We only check whether it exists; the key itself is never shown.
    settings = get_settings()
    if settings.llm_provider == "gemini" and not settings.has_gemini_key:
        st.warning("No Gemini API key found yet. Add GEMINI_API_KEY to your .env file.")


def main() -> None:
    """Set up the page, then draw the sidebar and the main area."""
    st.set_page_config(page_title=APP_NAME, page_icon="🔍", layout="wide")
    show_sidebar()
    show_main_page()


main()
