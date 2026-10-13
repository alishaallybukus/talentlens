"""Home: the landing page. What TalentLens does, where you are in the flow, the live numbers, and the pipeline.

Why this file exists:
- A recruiter (or a grader) opening the app should see in a few seconds what
  it does, how far the current screening has got, and what to do next.
- The pipeline diagram shows which steps are AI agents, which are plain code
  and where a human must decide, the core idea of the project.
- The charts give an overview of the current run: how candidates score against
  the band thresholds, and which requirements are rarest.
"""

from __future__ import annotations

import streamlit as st

from core.config import Settings
from core.memory import Memory
from core.report import count_decisions, screened_candidates
from ui import charts, components, nav, state
from ui.page_behind_the_scenes import latest_results_by_provider

STEP_HINTS: dict[str, str] = {
    "job_setup": "Load the job and approve the requirements",
    "candidates": "Add CVs (or use the sample CVs)",
    "screening": "Run the AI agents on every CV",
    "review": "Shortlist, hold or reject each candidate",
    "report": "Draft and approve the shortlist report",
    "behind": "See the trace, prompts and evaluation",
}

AUDIT_WORDS: dict[str, str] = {
    "requirements_approved": "approved the requirements",
    "decision_saved": "recorded a decision",
    "decisions_reset": "reset the decisions",
    "report_drafted": "drafted the report",
    "report_approved": "approved the report",
    "preference_added": "saved a preference",
    "preference_deleted": "deleted a preference",
    "new_screening": "started a new screening",
    "email_approved": "approved an email",
    "report_sent_n8n": "sent the report via n8n",
}


# ===========================================================================
# Hero and progress
# ===========================================================================


def steplist_html(memory: Memory) -> str:
    done = nav.done_steps(memory)
    upcoming = nav.next_step(memory)
    items = []
    for number, (key, label) in enumerate(nav.STEPS, start=1):
        css = "tl-done" if key in done else ("tl-next" if key == upcoming else "")
        mark = "✓" if key in done else str(number)
        items.append(
            f'<li class="{css}"><span class="tl-step-mark">{mark}</span>'
            f'<span><strong>{components.safe(label)}</strong> · '
            f'<span class="tl-muted">{components.safe(STEP_HINTS[key])}</span></span></li>'
        )
    return f'<ul class="tl-steplist">{"".join(items)}</ul>'


def show_hero(memory: Memory) -> None:
    upcoming = nav.next_step(memory)
    started = bool(nav.done_steps(memory))
    columns = st.columns([1.55, 1], gap="large")
    with columns[0], st.container(key="card-hero"):
        components.show(
            '<p class="tl-eyebrow">AI recruitment assistant · fictional demo data</p>'
            '<div class="tl-hero-title">AI does the reading.<br><span>You make the call.</span></div>'
            '<div class="tl-hero-sub">Specialist AI agents read each CV, match it to the job with word-for-word '
            "quotes and draft interview questions. Code checks every quote and calculates the scores. "
            "Only you shortlist or reject.</div>"
        )
        buttons = st.columns([1.5, 1.2, 1.2])
        label = f"Continue: {nav.STEP_LABELS[upcoming]} →" if started else "Start a screening →"
        buttons[0].button(label, type="primary", width="stretch", key="home_continue",
                          on_click=nav.go_to, args=(upcoming,))
        buttons[1].button("Review candidates", width="stretch", key="home_review", on_click=nav.go_to, args=("review",))
        buttons[2].button("How it works", width="stretch", key="home_behind", on_click=nav.go_to, args=("behind",))
    with columns[1], st.container(key="card-progress"):
        job = state.approved_requirements()
        run = state.current_run()
        title = job.title if job else (run.requirements.title if run else "No job yet")
        components.show(
            f'<p class="tl-section-title">Your screening</p>'
            f'<div class="tl-muted">{components.safe(title)}</div>{steplist_html(memory)}'
        )


# ===========================================================================
# Numbers
# ===========================================================================


def evaluation_score(provider: str) -> str:
    """'7 of 7' from the latest evaluation for this provider, or '-'."""
    result = latest_results_by_provider().get(provider)
    return result.get("summary", {}).get("passed", "-") if result else "-"


def show_numbers(memory: Memory) -> None:
    run = state.current_run()
    candidates = screened_candidates(run) if run else []
    decisions = memory.get_decisions(run.run_id) if run else {}
    counts = count_decisions(run, decisions) if run else {"Shortlist": 0}
    strong = sum(1 for candidate in candidates if candidate.score.band == "Strong match")
    provider = st.session_state.get("provider", "ollama")
    model = st.session_state.get("ollama_model" if provider == "ollama" else "gemini_model", "")
    components.show_kpis(
        [
            ("CVs loaded", len(memory.list_documents(kind="cv")), ""),
            ("Screened", len(candidates), "primary"),
            ("Strong matches", strong, "strong"),
            ("Shortlisted by you", counts["Shortlist"], "strong"),
            (f"Evaluation ({provider.title()})", evaluation_score(provider), "primary"),
        ]
    )
    st.caption(f"AI model: {provider.title()} · {model}. Change it in ⚙ Settings.")


# ===========================================================================
# Pipeline, charts and activity
# ===========================================================================


def show_pipeline(memory: Memory) -> None:
    with st.container(key="card-pipeline"):
        components.show('<p class="tl-section-title">How each CV is screened</p>')
        done = nav.done_steps(memory)
        states = {}
        if "job_setup" in done:
            states["approve"] = "done"
        if "screening" in done:
            for key in ["intake", "cv_analyst", "comparison", "reviewer", "scoring", "report"]:
                states[key] = "done"
        if "report" in done:
            states["decide"] = "done"
        components.show(components.pipeline_html(states) + components.pipeline_legend())


def show_charts(memory: Memory) -> None:
    run = state.current_run()
    candidates = screened_candidates(run) if run else []
    if not candidates:
        return
    decisions = memory.get_decisions(run.run_id)
    columns = st.columns(2, gap="medium")
    with columns[0], st.container(key="card-chart-scores"):
        components.show('<p class="tl-section-title">Scores against the bands</p>')
        st.altair_chart(charts.score_chart(candidates, decisions), width="stretch")
        st.caption("Calculated by code from the verified evidence. Dashed lines: the band thresholds.")
    with columns[1], st.container(key="card-chart-coverage"):
        components.show('<p class="tl-section-title">Requirement coverage</p>')
        st.altair_chart(charts.coverage_chart(run, candidates), width="stretch")
        st.caption("Share of candidates showing each requirement (partial counts as half). Hover for the counts.")


def show_activity(memory: Memory) -> None:
    rows = memory.list_audit(limit=6)
    if not rows:
        return
    with st.container(key="card-activity"):
        components.show('<p class="tl-section-title">Recent human actions</p>')
        items = []
        for row in rows:
            action = AUDIT_WORDS.get(row["event"], row["event"].replace("_", " "))
            detail = f' <span class="tl-muted">· {components.safe(row["detail"])}</span>' if row["detail"] else ""
            items.append(f'<li><time>{state.short_time(row["ts"])}</time>'
                         f'<span><strong>{components.safe(row["actor"])}</strong> {action}{detail}</span></li>')
        components.show(f'<ul class="tl-activity">{"".join(items)}</ul>')
        st.caption("Every human action is recorded in the audit log (Behind the scenes).")


def render(settings: Settings, memory: Memory) -> None:
    show_hero(memory)
    show_numbers(memory)
    show_pipeline(memory)
    show_charts(memory)
    show_activity(memory)
