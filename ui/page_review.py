"""Page 4, Review: the ranking, the comparison matrix, each candidate's evidence, and the recruiter's decisions.

Why this file exists:
- This is human Checkpoint 2. The AI only recommends a band; the recruiter
  records Shortlist, Hold or Reject for each candidate (FR-D1 to FR-D5).
  A rejection needs a written, job-related reason; the database refuses one without it.
- The recruiter can see WHY the AI said what it said: every requirement shows
  the exact CV quote, whether code verified that quote, and the guideline
  clauses that were cited (FR-R1 to FR-R6).

Layout: summary tiles, then a clickable ranking table (or the comparison matrix),
then the selected candidate's profile with tabs (Summary, Evidence, Flags and
intake) next to the decision card.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core.agents import run_missing_info_email
from core.config import Settings
from core.excel import build_workbook
from core.guardrails import MISSING_INFO_NAMES
from core.memory import ALLOWED_DECISIONS, DecisionError, Memory
from core.report import candidate_name, decision_for, screened_candidates
from core.schemas import CandidateResult, Requirement, RunResult
from ui import components, state

BANDS: list[str] = ["Strong match", "Possible match", "Not a match for this role"]
DECISION_LABELS: dict[str, str] = {"Pending": "○ Pending", "Shortlist": "✓ Shortlist", "Hold": "⏸ Hold", "Reject": "✕ Reject"}
EVIDENCE_FILTERS: list[str] = ["All", "✔ Met", "◐ Partial", "✖ Missing"]


# ===========================================================================
# Helpers
# ===========================================================================


def all_requirements(run: RunResult) -> list[Requirement]:
    return run.requirements.must_have + run.requirements.nice_to_have


def results_by_id(candidate: CandidateResult) -> dict:
    if candidate.assessment is None:
        return {}
    return {result.requirement_id: result for result in candidate.assessment.results}


def select_candidate(candidate_id: str) -> None:
    """Callback: show this candidate's details (runs before the select box is drawn)."""
    st.session_state["selected_candidate"] = candidate_id


# ===========================================================================
# Summary tiles (FR-D4)
# ===========================================================================


def show_summary(candidates: list[CandidateResult], decisions: dict[str, dict]) -> None:
    band_counts = {band: 0 for band in BANDS}
    for candidate in candidates:
        band_counts[candidate.score.band] += 1
    decided = sum(1 for candidate in candidates if decision_for(candidate, decisions) != "Pending")
    components.show_kpis(
        [
            ("Strong match", band_counts["Strong match"], "strong"),
            ("Possible match", band_counts["Possible match"], "possible"),
            ("Not a match", band_counts["Not a match for this role"], "notmatch"),
            ("Decided by you", f"{decided} of {len(candidates)}", "primary"),
        ]
    )
    st.progress(decided / len(candidates) if candidates else 0.0)


# ===========================================================================
# Ranking table (FR-R1): click a row to open that candidate
# ===========================================================================


def ranking_frame(candidates: list[CandidateResult], decisions: dict[str, dict]) -> pd.DataFrame:
    rows = []
    for rank, candidate in enumerate(candidates, start=1):
        profile = candidate.profile
        rows.append(
            {
                "#": rank,
                "Candidate": candidate_name(candidate),
                "Headline": profile.headline if profile and profile.headline else "",
                "Score": candidate.score.overall,
                "AI band (recommendation)": candidate.score.band,
                "Flags": " ".join(components.FLAG_ICONS.get(flag.code, "•") for flag in candidate.flags),
                "Your decision": DECISION_LABELS[decision_for(candidate, decisions)],
            }
        )
    return pd.DataFrame(rows)


def on_table_select(candidates: list[CandidateResult]) -> None:
    """Callback for the ranking table: the clicked row becomes the selected candidate."""
    selection = st.session_state.get("ranking_table")
    rows = selection.selection.rows if selection else []
    if rows:
        st.session_state["selected_candidate"] = candidates[rows[0]].candidate_id


def show_ranking(candidates: list[CandidateResult], decisions: dict[str, dict]) -> None:
    st.caption("Highest score first. **Click a row** to open that candidate below.")
    st.dataframe(
        ranking_frame(candidates, decisions),
        hide_index=True,
        width="stretch",
        key="ranking_table",
        on_select=lambda: on_table_select(candidates),
        selection_mode="single-row",
        column_config={
            "#": st.column_config.NumberColumn(width="small"),
            "Candidate": st.column_config.TextColumn(width="medium"),
            "Headline": st.column_config.TextColumn(width="medium"),
            "Score": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f", width="small"),
            "AI band (recommendation)": st.column_config.TextColumn(width="medium"),
            "Flags": st.column_config.TextColumn(width="small", help="Hover the candidate's flags in the detail view"),
            "Your decision": st.column_config.TextColumn(width="small"),
        },
    )


# ===========================================================================
# Comparison matrix (FR-R2)
# ===========================================================================


def matrix_html(run: RunResult, candidates: list[CandidateResult]) -> str:
    requirements = all_requirements(run)
    headers = ["Candidate", "Score"] + [
        f'<span title="{components.safe(requirement.label)}">{components.safe(requirement.id)}</span>'
        for requirement in requirements
    ]
    rows = []
    for candidate in candidates:
        results = results_by_id(candidate)
        row = [f"<strong>{components.safe(candidate_name(candidate))}</strong>", f"{candidate.score.overall:.0f}"]
        for requirement in requirements:
            result = results.get(requirement.id)
            row.append(components.status_dot(result.status if result else "missing"))
        rows.append(row)
    return components.html_table(headers, rows, css_class="tl-matrix")


def show_matrix(run: RunResult, candidates: list[CandidateResult]) -> None:
    st.caption("✔ met · ◐ partial · ✖ missing. Hover a requirement id to see its name.")
    components.show(matrix_html(run, candidates))
    legend = " ".join(
        components.chip(f"{requirement.id} {requirement.label}", "neutral") for requirement in all_requirements(run)
    )
    components.show(f"<div>{legend}</div>")


# ===========================================================================
# Candidate profile (FR-R3 to FR-R5)
# ===========================================================================


def profile_header_html(candidate: CandidateResult, decision: str) -> str:
    profile = candidate.profile
    safe = components.safe
    meta = " · ".join(part for part in [profile.headline if profile else "", profile.location if profile else ""] if part)
    flags = components.flag_icons(candidate.flags)
    return (
        '<div class="tl-profile">'
        f"{components.score_ring(candidate.score.overall, size=84)}"
        "<div>"
        f'<div class="tl-profile-name">{safe(candidate_name(candidate))}</div>'
        f'<div class="tl-profile-meta">{safe(meta)}</div>'
        f"<div>{components.band_pill(candidate.score.band)} {components.decision_chip(decision)} {flags}</div>"
        f"<div>{components.ai_note()}</div>"
        "</div></div>"
    )


def question_purpose(purpose: str, labels: dict[str, str]) -> str:
    """The Report Agent often gives a requirement id as the purpose ("M4"): add its name."""
    key = purpose.strip()
    return f"{key} · {labels[key]}" if key in labels else purpose


def brief_html(candidate: CandidateResult, labels: dict[str, str]) -> str:
    """The brief's format: Candidate, Relevant Experience, Key Skills, Missing Information, Questions."""
    report = candidate.report
    safe = components.safe
    summary = f"<p>{safe(report.summary)}</p>" if report and report.summary else ""
    experience = safe(report.relevant_experience) if report else "Not stated"
    skills = report.key_skills if report else []
    skills_html = " ".join(components.chip(skill, "primary") for skill in skills) or "None listed"
    missing = [MISSING_INFO_NAMES.get(key, key) for key in candidate.missing_info]
    missing_html = " ".join(components.chip(f"❓ {item}", "partial") for item in missing) or components.chip("✔ Nothing missing", "met")
    questions = report.interview_questions if report else []
    questions_html = "".join(
        f"<li>{safe(question.question)}<br><span class='tl-muted'>Why: {safe(question_purpose(question.purpose, labels))}</span></li>"
        for question in questions
    )
    return (
        '<div class="tl-brief">'
        f"<h4>Candidate</h4>{summary}"
        '<div class="tl-brief-grid">'
        f'<div class="tl-brief-item"><h4>Relevant experience</h4><div>{experience}</div></div>'
        f'<div class="tl-brief-item"><h4>Missing information</h4><div>{missing_html}</div></div>'
        f'<div class="tl-brief-item" style="grid-column: 1 / -1"><h4>Key skills</h4><div>{skills_html}</div></div>'
        "</div>"
        f'<h4 style="margin-top:14px">Questions for interview</h4><ol class="tl-questions">{questions_html}</ol>'
        "</div>"
    )


def show_summary_tab(run: RunResult, candidate: CandidateResult) -> None:
    labels = {requirement.id: requirement.label for requirement in all_requirements(run)}
    columns = st.columns([1.6, 1], gap="large")
    with columns[0]:
        components.show(brief_html(candidate, labels))
    with columns[1]:
        score = candidate.score
        components.show(
            '<div class="tl-brief"><h4>Score breakdown</h4></div>'
            + components.bar_row("Must-haves", score.must_have_pct)
            + components.bar_row("Nice-to-haves", score.nice_to_have_pct)
            + components.bar_row("Overall", score.overall)
        )
        st.caption("Calculated by code (70% must-haves, 30% nice-to-haves; bands at 75 and 55), never by the AI.")
        if score.must_have_gaps:
            st.markdown("**Must-have gaps:** " + ", ".join(score.must_have_gaps))
        if candidate.assessment is not None:
            st.markdown("**Strengths**")
            for item in candidate.assessment.strengths or ["None listed"]:
                st.markdown(f"- {item}")
            st.markdown("**Concerns**")
            for item in candidate.assessment.concerns or ["None listed"]:
                st.markdown(f"- {item}")


def verified_badge(result) -> str:
    if result.status == "missing":
        return ""
    if result.verified:
        return components.chip("✔ Verified", "met")
    return components.chip("✖ Not verified", "missing")


def evidence_item(requirement: Requirement, result) -> str:
    """One requirement as a stacked item: name and badges, then the quote, then the reasoning."""
    safe = components.safe
    index = state.current_index()
    kind = "Must-have" if requirement.id.startswith("M") else "Nice-to-have"
    head = f'<span class="tl-ev-req"><strong>{safe(requirement.id)}</strong> {safe(requirement.label)}</span>'
    if result is None:
        return (f'<div class="tl-ev"><div class="tl-ev-head">{head}{components.status_chip("missing")}</div>'
                f'<div class="tl-ev-why">No result.</div></div>')
    clauses = "".join(
        components.clause_chip(ref, index.get(ref).text if index.get(ref) else None) for ref in result.guideline_refs
    )
    quote = f'<div class="tl-quote tl-ev-quote">“{safe(result.evidence)}”</div>' if result.evidence else ""
    reasoning = safe(result.reasoning)
    if result.original_status:
        reasoning += f" <em>Changed by code from “{safe(result.original_status)}”.</em>"
    return (
        '<div class="tl-ev"><div class="tl-ev-head">'
        f"{head}{components.status_chip(result.status)}{verified_badge(result)}"
        f'<span class="tl-ev-kind">{kind}</span></div>'
        f'{quote}<div class="tl-ev-why">{reasoning} {clauses}</div></div>'
    )


def show_evidence_tab(run: RunResult, candidate: CandidateResult) -> None:
    st.caption("Each “met” or “partial” needs a quote that code found in the CV. Click a § chip to read the clause.")
    with st.container(key="evidence-filter"):
        choice = st.radio("Show", EVIDENCE_FILTERS, horizontal=True, key=f"evidence_filter_{candidate.candidate_id}",
                          label_visibility="collapsed")
    wanted = {"✔ Met": "met", "◐ Partial": "partial", "✖ Missing": "missing"}.get(choice)
    results = results_by_id(candidate)
    items = []
    for requirement in all_requirements(run):
        result = results.get(requirement.id)
        status = result.status if result else "missing"
        if wanted is None or status == wanted:
            items.append(evidence_item(requirement, result))
    if not items:
        st.info("No requirements with this status.")
        return
    components.show('<div class="tl-ev-list">' + "".join(items) + "</div>")


def show_flags_tab(candidate: CandidateResult) -> None:
    st.markdown("**Flags**")
    if candidate.flags:
        components.show("<div>" + "<br>".join(components.flag_chip(flag) for flag in candidate.flags) + "</div>")
    else:
        st.markdown("No flags.")
    st.markdown("**Intake notes**")
    if candidate.redactions:
        st.markdown("Removed before the AI saw the CV: " + ", ".join(candidate.redactions)
                    + ". Only the labels are kept, never the details.")
    else:
        st.markdown("No protected details were found.")
    if candidate.quarantined_text:
        st.warning("Text that looked like instructions to the AI was removed and never sent to it:")
        for line in candidate.quarantined_text:
            components.show(f'<div class="tl-warning-box">{components.safe(line)}</div>')


# ===========================================================================
# Decision card (FR-D1 to FR-D3)
# ===========================================================================


def save_decision(memory: Memory, run: RunResult, candidate: CandidateResult) -> None:
    decision = st.session_state[f"decision_{candidate.candidate_id}"]
    reason = st.session_state.get(f"reason_{candidate.candidate_id}", "")
    try:
        memory.save_decision(
            run.run_id,
            candidate.candidate_id,
            candidate_name(candidate),
            decision,
            reason,
            state.reviewer_name(),
            candidate.score.band,
            candidate.score.overall,
        )
    except DecisionError as error:
        st.error(str(error))
        return
    st.toast(f"Decision saved: {candidate_name(candidate)} → {decision}")
    st.rerun()


def show_decision_card(memory: Memory, run: RunResult, candidate: CandidateResult, decisions: dict) -> None:
    saved = decisions.get(candidate.candidate_id)
    with st.container(key="card-decision"):
        st.markdown('<p class="tl-section-title">Your decision</p>', unsafe_allow_html=True)
        st.caption("The AI never shortlists or rejects anyone. Only your recorded decision does.")
        current = saved["decision"] if saved else "Pending"
        st.radio("Decision", ALLOWED_DECISIONS, index=ALLOWED_DECISIONS.index(current), horizontal=True,
                 key=f"decision_{candidate.candidate_id}", format_func=lambda option: DECISION_LABELS[option],
                 label_visibility="collapsed")
        st.text_area(
            "Reason or notes",
            value=saved["reason"] if saved and saved["reason"] else "",
            key=f"reason_{candidate.candidate_id}",
            height=96,
            placeholder="A rejection needs a job-related reason (at least 10 characters).",
        )
        if st.button("Save decision", type="primary", key=f"save_{candidate.candidate_id}", width="stretch"):
            save_decision(memory, run, candidate)
        if saved:
            ai_score = f", {saved['ai_score']:.1f}" if saved["ai_score"] is not None else ""
            st.caption(
                f"Saved: {saved['decision']} by {saved['reviewer']} at {state.short_time(saved['decided_at'])} "
                f"(the AI said {saved['ai_band']}{ai_score})"
            )


# ===========================================================================
# Missing-information email draft (extra, FR-X6)
# ===========================================================================


def draft_email(settings: Settings, memory: Memory, run: RunResult, candidate: CandidateResult) -> None:
    llm = state.make_llm(settings, memory)
    missing = [MISSING_INFO_NAMES.get(key, key) for key in candidate.missing_info]
    try:
        with st.spinner("The Report Agent is drafting the email..."):
            step = run_missing_info_email(llm, run.requirements.title, run.requirements.company or "",
                                          candidate_name(candidate), missing, state.reviewer_name())
    except Exception as error:  # never a raw error
        st.error(state.friendly_error(error))
        return
    # Allowed: the subject and body boxes are drawn after this button.
    st.session_state[f"email_subject_{candidate.candidate_id}"] = step.result.subject
    st.session_state[f"email_body_{candidate.candidate_id}"] = step.result.body


def approve_email(memory: Memory, run: RunResult, candidate: CandidateResult) -> None:
    subject = st.session_state.get(f"email_subject_{candidate.candidate_id}", "")
    memory.add_audit(state.reviewer_name(), "email_approved", f"{candidate_name(candidate)}: {subject}", run.run_id)
    st.toast("Email approved and logged. Copy it into your email app to send it.")


def show_email_card(settings: Settings, memory: Memory, run: RunResult, candidate: CandidateResult) -> None:
    if not candidate.missing_info:
        return
    cid = candidate.candidate_id
    with st.container(key="card-email"):
        st.markdown('<p class="tl-section-title">Ask for the missing details</p>', unsafe_allow_html=True)
        st.caption("Drafts an email asking for exactly what's missing. It is never sent automatically.")
        if st.button("✉ Draft email", key=f"draft_email_{cid}", width="stretch"):
            draft_email(settings, memory, run, candidate)
        if f"email_body_{cid}" not in st.session_state:
            return
        st.text_input("Subject", key=f"email_subject_{cid}")
        st.text_area("Email", key=f"email_body_{cid}", height=220)
        with st.expander("Copy"):
            st.code(st.session_state[f"email_subject_{cid}"] + "\n\n" + st.session_state[f"email_body_{cid}"],
                    language=None, wrap_lines=True)
        st.button("Approve email", key=f"approve_email_{cid}", on_click=approve_email, args=(memory, run, candidate))


# ===========================================================================
# Reset decisions (FR-D5)
# ===========================================================================


def show_reset_decisions(memory: Memory, run: RunResult) -> None:
    if not st.session_state.get("confirm_reset_decisions"):
        if st.button("Reset decisions", width="stretch"):
            st.session_state["confirm_reset_decisions"] = True
            st.rerun()
        return
    st.warning("Clear every decision for this run? This is logged in the audit log.")
    columns = st.columns(2)
    if columns[0].button("Yes, reset"):
        memory.reset_decisions(run.run_id, actor=state.reviewer_name())
        for candidate in run.candidates:  # forget the old values shown in the decision boxes
            st.session_state.pop(f"decision_{candidate.candidate_id}", None)
            st.session_state.pop(f"reason_{candidate.candidate_id}", None)
        st.session_state["confirm_reset_decisions"] = False
        st.toast("Decisions reset.")
        st.rerun()
    if columns[1].button("Cancel"):
        st.session_state["confirm_reset_decisions"] = False
        st.rerun()


# ===========================================================================
# The page
# ===========================================================================


def show_candidate_switcher(candidates: list[CandidateResult]) -> CandidateResult:
    """‹ previous · candidate picker · next › — quick movement between candidates."""
    ids = [candidate.candidate_id for candidate in candidates]
    if st.session_state.get("selected_candidate") not in ids:
        st.session_state["selected_candidate"] = ids[0]
    position = ids.index(st.session_state["selected_candidate"])
    names = {candidate.candidate_id: candidate_name(candidate) for candidate in candidates}
    columns = st.columns([1, 5, 1], vertical_alignment="bottom")
    columns[0].button("‹ Previous", key="candidate_prev", width="stretch", disabled=position == 0,
                      on_click=select_candidate, args=(ids[max(position - 1, 0)],))
    columns[1].selectbox("Candidate", ids, format_func=lambda cid: f"#{ids.index(cid) + 1}  {names[cid]}",
                         key="selected_candidate", label_visibility="collapsed")
    columns[2].button("Next ›", key="candidate_next", width="stretch", disabled=position == len(ids) - 1,
                      on_click=select_candidate, args=(ids[min(position + 1, len(ids) - 1)],))
    selected_id = st.session_state["selected_candidate"]
    return next(candidate for candidate in candidates if candidate.candidate_id == selected_id)


def render(settings: Settings, memory: Memory) -> None:
    components.page_header(
        "Step 4 of 6 · Checkpoint 2",
        "Review",
        "Check the evidence and record your decision for each candidate. "
        "The band is the AI's recommendation; <b>the decision is yours</b>.",
    )
    run = state.current_run()
    candidates = screened_candidates(run) if run else []
    if not candidates:
        st.info("No screened candidates yet. Run the screening in step 3 first.")
        return

    decisions = memory.get_decisions(run.run_id)
    failed = [candidate for candidate in run.candidates if candidate.error]
    if failed:
        st.warning(f"{len(failed)} CV(s) couldn't be screened. Retry them in step 3.")

    show_summary(candidates, decisions)
    tabs = st.tabs(["Ranking", "Comparison matrix"])
    with tabs[0]:
        show_ranking(candidates, decisions)
    with tabs[1]:
        show_matrix(run, candidates)

    st.markdown("&nbsp;")
    selected = show_candidate_switcher(candidates)
    decision = decision_for(selected, decisions)
    columns = st.columns([2.3, 1], gap="large")
    with columns[0], st.container(key="card-profile"):
        components.show(profile_header_html(selected, decision))
        detail_tabs = st.tabs(["Summary", "Evidence", "Flags & intake"])
        with detail_tabs[0]:
            show_summary_tab(run, selected)
        with detail_tabs[1]:
            show_evidence_tab(run, selected)
        with detail_tabs[2]:
            show_flags_tab(selected)
    with columns[1]:
        show_decision_card(memory, run, selected, decisions)
        show_email_card(settings, memory, run, selected)
        with st.container(key="card-tools"):
            st.markdown('<p class="tl-section-title">Tools</p>', unsafe_allow_html=True)
            st.download_button("⬇ Download Excel", build_workbook(run, decisions), file_name="talentlens_screening.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               help="Ranking, comparison matrix and evidence, one sheet each", width="stretch")
            show_reset_decisions(memory, run)
