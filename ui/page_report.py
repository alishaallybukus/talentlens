"""Page 5, Shortlist report: draft, edit and approve the report for the hiring manager, then download it.

Why this file exists:
- This is human Checkpoint 3 (FR-P1 to FR-P5). The report only ever includes
  candidates the recruiter shortlisted; everyone else is just a count.
- The Report Agent writes the overview; code adds one section per shortlisted
  candidate (core/report.py). The recruiter edits the Markdown with a live
  preview, and downloads appear only after they click Approve report.
"""

from __future__ import annotations

import re

import requests
import streamlit as st

from core.agents import run_shortlist_overview
from core.config import Settings
from core.memory import Memory
from core.report import (
    build_report_markdown,
    count_decisions,
    markdown_to_html_page,
    overview_input,
    shortlisted_candidates,
    with_footer,
)
from core.schemas import RunResult
from ui import components, state

EDITOR_KEY = "report_editor"


# ===========================================================================
# Keeping the editor in step with the saved report
# ===========================================================================


def load_editor(memory: Memory, run: RunResult) -> None:
    """Fill the editor from the database the first time this run's report is opened."""
    if st.session_state.get("report_editor_run") == run.run_id:
        return
    saved = memory.get_report(run.run_id)
    text = ""
    if saved:
        text = saved["final_md"] or saved["draft_md"] or ""
    st.session_state[EDITOR_KEY] = text
    st.session_state["report_editor_run"] = run.run_id


def draft_report(settings: Settings, memory: Memory, run: RunResult) -> None:
    """Ask the Report Agent for the overview, then build the full draft in code (FR-P2)."""
    decisions = memory.get_decisions(run.run_id)
    llm = state.make_llm(settings, memory)
    try:
        with st.spinner("The Report Agent is writing the overview..."):
            step = run_shortlist_overview(llm, run.requirements.title, overview_input(run, decisions))
    except Exception as error:  # a friendly message, never a raw Python error
        st.error(state.friendly_error(error))
        return
    markdown_text = build_report_markdown(run, decisions, step.result, state.reviewer_name(), state.today_text())
    memory.save_report_draft(run.run_id, markdown_text, actor=state.reviewer_name())
    st.session_state[EDITOR_KEY] = markdown_text  # allowed: the editor isn't drawn yet in this run
    st.toast("Draft report ready. Edit it, then approve it.")


def approve_report(memory: Memory, run: RunResult) -> None:
    """Button callback (FR-P3): record the approval and stamp the footer with the approver and date."""
    final_text = with_footer(st.session_state.get(EDITOR_KEY, ""), state.reviewer_name(), state.today_text())
    memory.approve_report(run.run_id, final_text, approved_by=state.reviewer_name())
    st.session_state[EDITOR_KEY] = final_text  # callbacks run before the editor is drawn
    st.toast("Report approved. You can download it now.")


# ===========================================================================
# Page sections
# ===========================================================================


def show_counts(counts: dict[str, int]) -> None:
    components.show_kpis(
        [
            ("Shortlisted", counts["Shortlist"], "strong"),
            ("On hold", counts["Hold"], "possible"),
            ("Rejected", counts["Reject"], "notmatch"),
            ("Pending", counts["Pending"], ""),
        ]
    )


def file_stem(title: str) -> str:
    """'Marketing Executive' -> 'shortlist_marketing_executive'."""
    words = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return f"shortlist_{words or 'report'}"


def show_downloads(settings: Settings, memory: Memory, run: RunResult) -> None:
    saved = memory.get_report(run.run_id)
    if not saved or not saved["approved_at"]:
        st.caption("Downloads appear after you approve the report.")
        return
    components.show(components.status_line(
        f"Approved by {saved['approved_by']} at {state.short_time(saved['approved_at'])}"
    ))
    if st.session_state.get(EDITOR_KEY, "") != saved["final_md"]:
        st.info("You changed the text after approving it. Approve again to update the downloads.")
    stem = file_stem(run.requirements.title)
    columns = st.columns([1, 1, 3])
    columns[0].download_button("Download .md", saved["final_md"], file_name=f"{stem}.md", mime="text/markdown")
    columns[1].download_button(
        "Download .html",
        markdown_to_html_page(saved["final_md"], f"Shortlist: {run.requirements.title}"),
        file_name=f"{stem}.html",
        mime="text/html",
    )
    columns[2].caption("Open the HTML file in your browser and print it to save a PDF.")
    if settings.n8n_webhook_url:
        if st.button("Send to hiring manager (n8n)"):
            send_to_n8n(settings, memory, run, saved)


def send_to_n8n(settings: Settings, memory: Memory, run: RunResult, saved: dict) -> None:
    """FR-X2: post the approved report to the n8n webhook, which emails the hiring manager."""
    payload = {
        "subject": f"Shortlist: {run.requirements.title}",
        "job_title": run.requirements.title,
        "report_markdown": saved["final_md"],
        "report_html": markdown_to_html_page(saved["final_md"], f"Shortlist: {run.requirements.title}"),
        "approved_by": saved["approved_by"],
        "approved_at": saved["approved_at"],
    }
    try:
        response = requests.post(settings.n8n_webhook_url, json=payload, timeout=30)
    except requests.RequestException:
        st.error("Couldn't reach n8n. Is n8n running, and is the workflow active?")
        return
    if response.status_code >= 400:
        st.error(f"n8n answered with an error ({response.status_code}). Check the workflow in n8n.")
        return
    memory.add_audit(state.reviewer_name(), "report_sent_n8n", "Sent to the hiring manager via n8n", run.run_id)
    st.success(f"Sent to n8n ({response.status_code}). The hiring manager's email is on its way.")


def show_editor(memory: Memory, run: RunResult) -> None:
    columns = st.columns(2)
    with columns[0]:
        st.markdown("**Edit (Markdown)**")
        st.text_area("Report text", key=EDITOR_KEY, height=560, label_visibility="collapsed")
    with columns[1]:
        st.markdown("**Preview**")
        with st.container(height=560, key="card-preview"):
            st.markdown(st.session_state.get(EDITOR_KEY, ""))
    st.button("Approve report", type="primary", on_click=approve_report, args=(memory, run),
              disabled=not st.session_state.get(EDITOR_KEY, "").strip(),
              help="Records your name and the time, and makes the downloads available")


def render(settings: Settings, memory: Memory) -> None:
    components.page_header(
        "Step 5 of 6 · Checkpoint 3",
        "Shortlist report",
        "Draft the report for the hiring manager. It includes <b>only the candidates you shortlisted</b>; "
        "the others appear as counts. Edit it, approve it, then download it.",
    )
    run = state.current_run()
    if run is None:
        st.info("No screening yet. Run the screening in step 3, then make your decisions in step 4.")
        return

    decisions = memory.get_decisions(run.run_id)
    counts = count_decisions(run, decisions)
    show_counts(counts)
    if not shortlisted_candidates(run, decisions):
        st.info("Shortlist at least one candidate in step 4 (Review) to draft the report.")
        return
    if counts["Pending"]:
        st.warning(f"{counts['Pending']} candidate(s) are still pending. You can still draft the report.")

    load_editor(memory, run)
    if st.button("Draft report", help="The Report Agent writes the overview; code adds each shortlisted candidate"):
        draft_report(settings, memory, run)
    if not st.session_state.get(EDITOR_KEY, "").strip():
        st.info("Click **Draft report** to start.")
        return
    show_editor(memory, run)
    show_downloads(settings, memory, run)
