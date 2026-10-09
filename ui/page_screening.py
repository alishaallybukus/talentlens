"""Page 3, Screening: run the agents on every CV and watch them work in a live timeline.

Why this file exists:
- "Run screening" sends each unscreened CV through the pipeline, one at a time
  (CV Analyst → Comparison → Reviewer → Report). "Screen new CVs" adds CVs
  uploaded later to the same run and ranking (FR-S1 to FR-S6).
- The timeline is built from the run's trace events, so the same view works
  live during a run and after a restart.
- A CV that fails shows an error row with a Retry button; the others carry on.
"""

from __future__ import annotations

import streamlit as st

from core.config import Settings
from core.memory import Memory
from core.schemas import RunResult, TraceEvent
from core.supervisor import create_run, retry_candidate, run_screening, screen_new_cvs
from ui import components, state

# The four steps shown for each candidate, and the label each gets on screen.
TIMELINE_STEPS: list[tuple[str, str]] = [
    ("CV Analyst", "CV Analyst"),
    ("Comparison", "Comparison"),
    ("Guardrail Reviewer", "Reviewer"),
    ("Report", "Report"),
]
STEP_AGENTS = [agent for agent, _label in TIMELINE_STEPS]


# ===========================================================================
# Building the timeline from trace events
# ===========================================================================


def new_row(file_name: str) -> dict:
    return {
        "name": file_name,
        "steps": {agent: {"status": "waiting", "ms": 0} for agent in STEP_AGENTS},
        "note": "",
    }


def apply_event(row: dict, event: TraceEvent) -> None:
    """Update one candidate's row with one trace event."""
    step = row["steps"][event.agent]
    if event.candidate:
        row["name"] = event.candidate  # the real name, once the CV Analyst has read it
    if event.status == "started":
        step["status"] = "running"
    elif event.status == "warning" and event.agent == "Guardrail Reviewer":
        step["status"] = "running"
        row["note"] = "Quotes not found; revising once"
    elif event.status in ("done", "revised"):
        if step["status"] != "revised":  # keep the "Revised" chip visible
            step["status"] = event.status
        step["ms"] += event.duration_ms


def mark_error(row: dict, message: str | None) -> None:
    """The first unfinished step becomes the error step."""
    for agent in STEP_AGENTS:
        if row["steps"][agent]["status"] not in ("done", "revised"):
            row["steps"][agent]["status"] = "error"
            break
    row["note"] = message or "Error"


def build_timeline(trace: list[TraceEvent]) -> list[dict]:
    """One row per CV, in the order they were screened. A retried CV keeps its place."""
    rows: dict[str, dict] = {}
    current_file: str | None = None
    for event in trace:
        if event.agent == "CV Analyst" and event.status == "started":
            current_file = event.candidate or ""
            rows[current_file] = new_row(current_file)  # a retry starts the row again
            apply_event(rows[current_file], event)
        elif event.agent == "Supervisor" and event.status == "error" and event.candidate in rows:
            mark_error(rows[event.candidate], event.detail)
        elif event.agent == "Supervisor" and event.action.startswith("Finished"):
            current_file = None
        elif current_file is not None and event.agent in STEP_AGENTS:
            apply_event(rows[current_file], event)
    return list(rows.values())


def timeline_html(trace: list[TraceEvent]) -> str:
    rows_html = []
    for row in build_timeline(trace):
        chips = []
        for agent, label in TIMELINE_STEPS:
            step = row["steps"][agent]
            chips.append(components.step_chip(label, step["status"], step["ms"]))
        rows_html.append(components.timeline_row(row["name"], chips, row["note"]))
    if not rows_html:
        return '<p class="tl-muted">No CVs screened yet.</p>'
    return '<div class="tl-card">' + "".join(rows_html) + "</div>"


# ===========================================================================
# Statistics (FR-S6)
# ===========================================================================


def show_statistics(run: RunResult) -> None:
    """Five headline numbers, then the reliability counters as a quiet row of chips (FR-S6)."""
    stats = run.stats
    minutes, seconds = divmod(int(stats.get("total_seconds", 0)), 60)
    components.show_kpis(
        [
            ("Candidates", stats.get("candidates", 0), "primary"),
            ("AI calls", stats.get("ai_calls", 0), ""),
            ("Cache hits", stats.get("cache_hits", 0), ""),
            ("Tokens in / out", f"{stats.get('tokens_in', 0):,} / {stats.get('tokens_out', 0):,}", ""),
            ("Total time", f"{minutes}m {seconds:02d}s", ""),
        ]
    )
    counters = [
        ("JSON repairs", stats.get("repairs", 0)),
        ("Revisions", stats.get("revisions", 0)),
        ("Retries", stats.get("retries", 0)),
        ("Model fallbacks", stats.get("fallbacks", 0)),
        ("Errors", stats.get("errors", 0)),
    ]
    chips = "".join(
        components.chip(f"{label}: {value}", "missing" if label == "Errors" and value else "neutral")
        for label, value in counters
    )
    components.show(f'<div style="margin-top:8px">{chips}</div>')


# ===========================================================================
# Running
# ===========================================================================


def run_needs_new(run: RunResult | None, requirements) -> bool:
    """A new run is needed if there's none yet, or the approved requirements changed."""
    return run is None or run.requirements.model_dump() != requirements.model_dump()


def start_screening(settings: Settings, memory: Memory, only_new: bool) -> None:
    requirements = state.approved_requirements()
    run = state.current_run()
    llm = state.make_llm(settings, memory)
    if run_needs_new(run, requirements):
        run = create_run(requirements, llm.provider, llm.model)
        st.session_state["run"] = run

    documents = state.active_cvs(memory)
    job_id = st.session_state.get("job_id")
    st.warning("Screening in progress. Please don't click elsewhere until it finishes.")
    timeline_placeholder = st.empty()
    timeline_placeholder.markdown(timeline_html(run.trace), unsafe_allow_html=True)

    def on_event(event: TraceEvent) -> None:
        # Redraw the timeline after every agent step (the live view, FR-S2).
        timeline_placeholder.markdown(timeline_html(run.trace), unsafe_allow_html=True)

    def save(saved_run: RunResult) -> None:
        memory.save_run(saved_run, job_id)

    try:
        if only_new:
            screen_new_cvs(run, documents, llm, state.current_index(), on_event, save)
        else:
            run_screening(run, documents, llm, state.current_index(), on_event, save)
    except Exception as error:  # the Supervisor isolates errors per CV; this is a last safety net
        st.error(state.friendly_error(error))
        return
    st.toast("Screening finished. Results are saved.")
    st.rerun()


def show_error_rows(settings: Settings, memory: Memory, run: RunResult) -> None:
    failed = [candidate for candidate in run.candidates if candidate.error]
    if not failed:
        return
    st.markdown("#### CVs that need a retry")
    documents_by_id = {intake.content_hash[:12]: intake for intake in state.active_cvs(memory)}
    for candidate in failed:
        with st.container(key=f"card-error-{candidate.candidate_id}"):
            columns = st.columns([5, 1])
            columns[0].markdown(f"**{candidate.file_name}**: {candidate.error}")
            intake = documents_by_id.get(candidate.candidate_id)
            if columns[1].button("Retry", key=f"retry_{candidate.candidate_id}", disabled=intake is None,
                                 help=None if intake else "This CV was removed from the Candidates page"):
                llm = state.make_llm(settings, memory)
                with st.spinner(f"Screening {candidate.file_name} again..."):
                    retry_candidate(run, intake, llm, state.current_index(),
                                    save_run=lambda r: memory.save_run(r, st.session_state.get("job_id")))
                st.rerun()


def screening_blockers(memory: Memory) -> list[str]:
    """Why the Run button is disabled, in plain words (FR-J5)."""
    reasons = []
    if state.approved_requirements() is None:
        reasons.append("Approve the requirements first (step 1).")
    if not memory.list_documents(kind="cv"):
        reasons.append("Add at least one CV (step 2).")
    return reasons


def render(settings: Settings, memory: Memory) -> None:
    components.page_header(
        "Step 3 of 6 · Multi-agent pipeline",
        "Screening",
        "For each CV: the <b>CV Analyst</b> extracts facts → the <b>Comparison Agent</b> checks each requirement "
        "with quotes → the <b>Reviewer</b> (code) verifies them → the <b>Report Agent</b> writes the brief. "
        "Scores are calculated by code, not by the AI.",
    )

    blockers = screening_blockers(memory)
    run = state.current_run()
    requirements = state.approved_requirements()
    is_current_run = run is not None and not run_needs_new(run, requirements) if requirements else False

    with st.container(key="card-run-actions"):
        columns = st.columns([1.1, 1.1, 3], vertical_alignment="center")
        run_clicked = columns[0].button("Run screening", type="primary", disabled=bool(blockers), width="stretch")
        new_clicked = columns[1].button(
            "Screen new CVs",
            disabled=bool(blockers) or not is_current_run,
            width="stretch",
            help="Adds CVs uploaded after the last run to the same ranking" if is_current_run
            else "Run screening first: new CVs join an existing run",
        )
        for reason in blockers:
            columns[2].caption(reason)
        if not blockers:
            columns[2].caption("CVs already screened with the same settings come back instantly from the cache.")
    if run is not None and requirements is not None and not is_current_run:
        st.info("The approved requirements changed since the last run, so **Run screening** will start a new run.")

    if run_clicked:
        start_screening(settings, memory, only_new=False)
        return
    if new_clicked:
        start_screening(settings, memory, only_new=True)
        return

    if run is None:
        st.info("No screening yet. Approve the requirements, add CVs, then click **Run screening**.")
        return

    components.show(
        '<p class="tl-section-title" style="margin-top:14px">Pipeline timeline</p>'
        + components.chip(f"Run {run.run_id}", "neutral") + components.chip(f"{run.provider} · {run.model}", "info")
        + components.chip(f"Started {state.short_time(run.created_at)}", "neutral")
    )
    components.show(timeline_html(run.trace))
    show_statistics(run)
    show_error_rows(settings, memory, run)
