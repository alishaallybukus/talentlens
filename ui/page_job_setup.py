"""Page 1, Job setup: load the job and guidelines, extract requirements, edit them, approve them.

Why this file exists:
- This is human Checkpoint 1. The Job Analyst Agent drafts the requirements
  checklist, the recruiter edits it, and only an approved checklist is used
  for screening (FR-J1 to FR-J7).
"""

from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

from core.agents import run_job_analyst
from core.config import Settings
from core.documents import DocumentReadError, read_document
from core.memory import Memory
from core.schemas import JobRequirements, Requirement
from ui import components, state

TYPE_MUST = "Must-have"
TYPE_NICE = "Nice-to-have"


# ===========================================================================
# Inputs: job description and guidelines (FR-J1, FR-J2)
# ===========================================================================


def show_job_description_input() -> None:
    st.markdown("#### Job description")
    source = st.radio(
        "Where is the job description?",
        ["Sample job", "Upload a file", "Paste text"],
        horizontal=True,
        key="jd_source_choice",
    )
    if source == "Sample job":
        if st.button("Load the sample job (Marketing Executive)"):
            st.session_state["jd_text"] = state.SAMPLE_JOB_FILE.read_text(encoding="utf-8")
            st.session_state["jd_source"] = "Sample job"
    elif source == "Upload a file":
        upload = st.file_uploader("Job description (.md, .txt, .pdf, .docx)", type=["md", "txt", "pdf", "docx"])
        if upload is not None and st.button("Use this file"):
            try:
                st.session_state["jd_text"] = read_document(upload.name, upload.getvalue())
                st.session_state["jd_source"] = upload.name
            except DocumentReadError as error:
                st.error(str(error))
    else:
        pasted = st.text_area("Paste the job description", height=200)
        if st.button("Use this text"):
            if len(pasted.split()) < 20:
                st.error("That's too short for a job description. Please paste the full text.")
            else:
                st.session_state["jd_text"] = pasted
                st.session_state["jd_source"] = "Pasted text"

    if st.session_state["jd_text"]:
        with st.expander(f"Job description loaded: {st.session_state['jd_source']}", expanded=False):
            st.markdown(st.session_state["jd_text"])


def show_guidelines_input(memory: Memory) -> None:
    st.markdown("#### Hiring guidelines")
    index = state.current_index()
    st.caption(
        f"Using: {st.session_state['guidelines_source']} ({len(index.clauses)} clauses indexed for retrieval)"
    )
    with st.expander("Change or view the guidelines"):
        upload = st.file_uploader("Upload guidelines (.md or .txt)", type=["md", "txt"], key="guidelines_upload")
        columns = st.columns(2)
        if upload is not None and columns[0].button("Use these guidelines"):
            text = upload.getvalue().decode("utf-8", errors="replace")
            state.save_guidelines(memory, upload.name, text)
            st.toast("Guidelines loaded and indexed.")
            st.rerun()
        if columns[1].button("Use the sample guidelines"):
            text = state.SAMPLE_GUIDELINES_FILE.read_text(encoding="utf-8")
            state.save_guidelines(memory, "Sample hiring guidelines", text)
            st.rerun()
        for clause in index.clauses:
            components.show(f"{components.clause_chip(clause.id, clause.text)} <span class='tl-muted'>{components.safe(clause.section_title)}</span>")


# ===========================================================================
# Extract requirements (FR-J3)
# ===========================================================================


def extract_requirements(settings: Settings, memory: Memory) -> None:
    preferences = [row["text"] for row in memory.list_preferences()]
    llm = state.make_llm(settings, memory)
    with st.spinner("The Job Analyst Agent is reading the job description..."):
        try:
            step = run_job_analyst(llm, st.session_state["jd_text"], preferences, state.current_index())
        except Exception as error:  # never show a raw error
            st.error(state.friendly_error(error))
            return
    st.session_state["draft_requirements"] = step.result
    cached = " (saved answer from the cache)" if step.meta.cache_hit else ""
    st.toast(f"Requirements extracted{cached}. Review and edit them below.")


# ===========================================================================
# The editable checklist (FR-J4) and approval (FR-J5)
# ===========================================================================


def requirements_to_table(requirements: JobRequirements) -> pd.DataFrame:
    rows = []
    for requirement_type, group in ((TYPE_MUST, requirements.must_have), (TYPE_NICE, requirements.nice_to_have)):
        for requirement in group:
            rows.append(
                {
                    "Type": requirement_type,
                    "Label": requirement.label,
                    "Description": requirement.description,
                    "Keywords": ", ".join(requirement.keywords),
                    "Min years": requirement.min_years,
                    "Weight": requirement.weight,
                }
            )
    return pd.DataFrame(rows)


def text_or_empty(value) -> str:
    """Table cells can be empty (None or NaN). Turn them into clean text."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def table_to_requirements(table: pd.DataFrame, base: JobRequirements, salary: dict) -> JobRequirements:
    """Turn the edited table back into requirements, numbering them M1.. and N1.. in order."""
    must: list[Requirement] = []
    nice: list[Requirement] = []
    for _, row in table.iterrows():
        label = text_or_empty(row.get("Label"))
        if not label:
            continue  # skip empty rows the recruiter added but didn't fill in
        min_years_text = text_or_empty(row.get("Min years"))
        weight_text = text_or_empty(row.get("Weight")) or "2"
        group = nice if row.get("Type") == TYPE_NICE else must
        prefix = "N" if group is nice else "M"
        keywords = [word.strip() for word in text_or_empty(row.get("Keywords")).split(",") if word.strip()]
        group.append(
            Requirement(
                id=f"{prefix}{len(group) + 1}",
                label=label,
                description=text_or_empty(row.get("Description")) or label,
                keywords=keywords,
                min_years=float(min_years_text) if min_years_text else None,
                weight=min(3, max(1, int(float(weight_text)))),
            )
        )
    return JobRequirements(
        title=salary["title"],
        company=base.company,
        location=base.location,
        salary_min=salary["salary_min"] or None,
        salary_max=salary["salary_max"] or None,
        currency=salary["currency"] or None,
        must_have=must,
        nice_to_have=nice,
        preference_notes=base.preference_notes,
    )


def show_editor(memory: Memory, draft: JobRequirements) -> None:
    st.markdown("#### Requirements checklist")
    st.caption(
        "Edit anything the AI got wrong: change a type, weight (1 to 3) or minimum years, "
        "add a row at the bottom, or select rows and delete them."
    )
    if draft.preference_notes:
        st.markdown("**Preference notes** (how your saved preferences changed the checklist):")
        for note in draft.preference_notes:
            st.markdown(f"- {note}")

    edited = st.data_editor(
        requirements_to_table(draft),
        num_rows="dynamic",
        width="stretch",
        hide_index=True,
        column_config={
            "Type": st.column_config.SelectboxColumn(options=[TYPE_MUST, TYPE_NICE], required=True, width="small"),
            "Label": st.column_config.TextColumn(required=True),
            "Description": st.column_config.TextColumn(width="large"),
            "Keywords": st.column_config.TextColumn(help="Comma-separated words a CV might contain"),
            "Min years": st.column_config.NumberColumn(min_value=0, max_value=30, step=0.5, width="small"),
            "Weight": st.column_config.NumberColumn(min_value=1, max_value=3, step=1, width="small"),
        },
        # A new key for each new draft, so edits from an older draft are never applied to it.
        key="requirements_editor_" + hashlib.md5(draft.model_dump_json().encode("utf-8")).hexdigest()[:10],
    )

    st.markdown("#### Job details and salary band")
    columns = st.columns([3, 1, 1, 1])
    salary = {
        "title": columns[0].text_input("Job title", value=draft.title),
        "salary_min": columns[1].number_input("Salary from", value=float(draft.salary_min or 0), step=1000.0),
        "salary_max": columns[2].number_input("Salary to", value=float(draft.salary_max or 0), step=1000.0),
        "currency": columns[3].text_input("Currency", value=draft.currency or "MUR"),
    }
    if not salary["salary_max"]:
        st.warning("No salary band yet. Please fill it in, so the salary check can work (§6.1).")

    if st.button("Approve requirements", type="primary"):
        approve(memory, edited, draft, salary)


def approve(memory: Memory, table: pd.DataFrame, draft: JobRequirements, salary: dict) -> None:
    """Checkpoint 1: save the approved checklist with the reviewer's name and the time."""
    try:
        requirements = table_to_requirements(table, draft, salary)
    except (ValueError, TypeError):
        st.error("Some numbers in the table couldn't be read. Check the Min years and Weight columns.")
        return
    if not requirements.must_have:
        st.error("Add at least one must-have requirement before approving.")
        return
    if not requirements.title.strip():
        st.error("Please give the job a title.")
        return

    reviewer = state.reviewer_name()
    job_id = memory.save_approved_job(st.session_state["jd_text"], requirements, approved_by=reviewer)
    job = memory.get_job(job_id)
    st.session_state["approved_requirements"] = requirements
    st.session_state["approved_by"] = reviewer
    st.session_state["approved_at"] = job["approved_at"]
    st.session_state["job_id"] = job_id
    st.session_state["draft_requirements"] = None
    st.toast("Requirements approved and saved.")
    st.rerun()


def show_approved(requirements: JobRequirements) -> None:
    approved_text = f"✔ Approved by {st.session_state['approved_by']} at {state.short_time(st.session_state['approved_at'])}"
    components.show(components.status_line(approved_text, "met"))
    st.markdown(
        f"**{requirements.title}**: {len(requirements.must_have)} must-haves, "
        f"{len(requirements.nice_to_have)} nice-to-haves. Salary band: "
        f"{requirements.currency or ''} {requirements.salary_min or 0:,.0f} to {requirements.salary_max or 0:,.0f}"
    )
    st.dataframe(requirements_to_table(requirements), width="stretch", hide_index=True)
    if st.button("Edit the requirements again"):
        st.session_state["draft_requirements"] = requirements
        st.session_state["approved_requirements"] = None
        st.rerun()


def offer_saved_checklist(memory: Memory) -> None:
    """FR-J6: if this job title was approved before, offer that checklist again."""
    draft = st.session_state["draft_requirements"]
    if draft is None:
        return
    saved = memory.get_job_by_title(draft.title)
    if saved is None:
        return
    label = f"Use the checklist approved by {saved['approved_by']} on {state.short_time(saved['approved_at'])}"
    if st.button(label):
        st.session_state["draft_requirements"] = saved["requirements"]
        st.rerun()


# ===========================================================================
# The page
# ===========================================================================


def render(settings: Settings, memory: Memory) -> None:
    st.header("1. Job setup")
    st.markdown(
        "Load the job description and hiring guidelines. The **Job Analyst Agent** drafts a requirements "
        "checklist; you edit it and approve it. Only an approved checklist is used for screening."
    )

    approved = state.approved_requirements()
    if approved is not None:
        show_approved(approved)
        return

    show_job_description_input()
    show_guidelines_input(memory)
    st.divider()

    has_job = bool(st.session_state["jd_text"])
    if st.button("Extract requirements", type="primary", disabled=not has_job,
                 help=None if has_job else "Load a job description first"):
        extract_requirements(settings, memory)
    if not has_job:
        st.caption("Load a job description first.")

    draft = st.session_state["draft_requirements"]
    if draft is not None:
        offer_saved_checklist(memory)
        show_editor(memory, draft)
