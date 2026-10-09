"""Page 4, Review: the ranking, the comparison matrix, each candidate's evidence, and the recruiter's decisions.

Why this file exists:
- This is human Checkpoint 2. The AI only recommends a band; the recruiter
  records Shortlist, Hold or Reject for each candidate (FR-D1 to FR-D5).
  A rejection needs a written, job-related reason; the database refuses one without it.
- The recruiter can see WHY the AI said what it said: every requirement shows
  the exact CV quote, whether code verified that quote, and the guideline
  clauses that were cited (FR-R1 to FR-R6).
"""

from __future__ import annotations

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
    """Button callback: show this candidate's details (runs before the select box is drawn)."""
    st.session_state["selected_candidate"] = candidate_id


# ===========================================================================
# Summary tiles and ranked cards (FR-R1, FR-D4)
# ===========================================================================


def show_summary(candidates: list[CandidateResult], decisions: dict[str, dict]) -> None:
    band_counts = {band: 0 for band in BANDS}
    for candidate in candidates:
        band_counts[candidate.score.band] += 1
    decided = sum(1 for candidate in candidates if decision_for(candidate, decisions) != "Pending")
    components.show_tiles(
        [
            ("Strong match", band_counts["Strong match"]),
            ("Possible match", band_counts["Possible match"]),
            ("Not a match", band_counts["Not a match for this role"]),
            ("Decided by you", f"{decided} of {len(candidates)}"),
        ],
        per_row=4,
    )
    st.progress(decided / len(candidates) if candidates else 0.0,
                text=f"{decided} of {len(candidates)} candidates decided")


def card_html(rank: int, candidate: CandidateResult, decision: str) -> str:
    profile = candidate.profile
    headline = profile.headline if profile and profile.headline else ""
    return (
        '<div class="tl-card tl-candidate-card">'
        f'<span class="tl-rank">#{rank}</span>'
        f"{components.score_ring(candidate.score.overall)}"
        '<div class="tl-card-body">'
        f'<div class="tl-card-title">{components.safe(candidate_name(candidate))}</div>'
        f'<div class="tl-muted">{components.safe(headline)}</div>'
        f"<div>{components.band_pill(candidate.score.band)} {components.ai_note()}</div>"
        "</div>"
        f"<div>{components.flag_icons(candidate.flags)}</div>"
        f"<div>{components.decision_chip(decision)}</div>"
        "</div>"
    )


def show_ranked_cards(candidates: list[CandidateResult], decisions: dict[str, dict]) -> None:
    st.markdown("#### Ranking")
    st.caption("Highest score first. Hover over an icon to read the flag.")
    for rank, candidate in enumerate(candidates, start=1):
        columns = st.columns([8, 1], vertical_alignment="center")
        with columns[0]:
            components.show(card_html(rank, candidate, decision_for(candidate, decisions)))
        columns[1].button("Details", key=f"open_{candidate.candidate_id}",
                          on_click=select_candidate, args=(candidate.candidate_id,))


# ===========================================================================
# Comparison matrix (FR-R2)
# ===========================================================================


def matrix_html(run: RunResult, candidates: list[CandidateResult]) -> str:
    requirements = all_requirements(run)
    headers = ["Candidate"] + [
        f'<span title="{components.safe(requirement.label)}">{components.safe(requirement.id)}</span>'
        for requirement in requirements
    ]
    rows = []
    for candidate in candidates:
        results = results_by_id(candidate)
        row = [f"<strong>{components.safe(candidate_name(candidate))}</strong>"]
        for requirement in requirements:
            result = results.get(requirement.id)
            row.append(components.status_chip(result.status if result else "missing"))
        rows.append(row)
    return components.html_table(headers, rows, css_class="tl-matrix")


def show_matrix(run: RunResult, candidates: list[CandidateResult]) -> None:
    st.markdown("#### Comparison matrix")
    components.show(matrix_html(run, candidates))
    legend = ", ".join(f"**{requirement.id}** {requirement.label}" for requirement in all_requirements(run))
    st.caption("M = must-have, N = nice-to-have. " + legend)


# ===========================================================================
# Candidate detail (FR-R3 to FR-R5)
# ===========================================================================


def brief_html(candidate: CandidateResult) -> str:
    """The brief's format: Candidate, Relevant Experience, Key Skills, Missing Information, Questions."""
    profile = candidate.profile
    report = candidate.report
    safe = components.safe

    candidate_lines = [f"<strong>{safe(candidate_name(candidate))}</strong>"]
    if profile and profile.headline:
        candidate_lines.append(safe(profile.headline))
    if profile and profile.location:
        candidate_lines.append(f"Location: {safe(profile.location)}")
    summary = f"<p>{safe(report.summary)}</p>" if report and report.summary else ""

    experience = safe(report.relevant_experience) if report else "Not stated"
    skills = report.key_skills if report else []
    skills_html = " ".join(components.chip(skill, "primary") for skill in skills) or "None listed"
    missing = [MISSING_INFO_NAMES.get(key, key) for key in candidate.missing_info]
    missing_html = " ".join(components.chip(f"❓ {item}", "partial") for item in missing) or "Nothing missing"
    questions = report.interview_questions if report else []
    questions_html = "".join(
        f"<li>{safe(question.question)}<br><span class='tl-muted'>Why: {safe(question.purpose)}</span></li>"
        for question in questions
    )

    return (
        '<div class="tl-card tl-brief">'
        f"<h4>Candidate</h4><div>{'<br>'.join(candidate_lines)}</div>{summary}"
        f"<h4>Relevant Experience</h4><div>{experience}</div>"
        f"<h4>Key Skills</h4><div>{skills_html}</div>"
        f"<h4>Missing Information</h4><div>{missing_html}</div>"
        f"<h4>Questions for Interview</h4><ol>{questions_html}</ol>"
        "</div>"
    )


def verified_badge(result) -> str:
    if result.status == "missing":
        return ""
    if result.verified:
        return components.chip("✔ Quote verified", "met")
    return components.chip("✖ Not verified", "missing")


def evidence_rows(run: RunResult, candidate: CandidateResult) -> list[list[str]]:
    safe = components.safe
    index = state.current_index()
    results = results_by_id(candidate)
    rows = []
    for requirement in all_requirements(run):
        result = results.get(requirement.id)
        if result is None:
            rows.append([safe(f"{requirement.id} {requirement.label}"), components.status_chip("missing"),
                         "", "", "No result", ""])
            continue
        quote = f'<span class="tl-quote">“{safe(result.evidence)}”</span>' if result.evidence else ""
        reasoning = safe(result.reasoning)
        if result.original_status:
            reasoning += f"<br><span class='tl-muted'>Changed by code from “{safe(result.original_status)}”.</span>"
        clauses = "".join(
            components.clause_chip(ref, index.get(ref).text if index.get(ref) else None)
            for ref in result.guideline_refs
        )
        rows.append([
            f"<strong>{safe(requirement.id)}</strong> {safe(requirement.label)}",
            components.status_chip(result.status),
            quote,
            verified_badge(result),
            reasoning,
            clauses,
        ])
    return rows


def show_score_breakdown(candidate: CandidateResult) -> None:
    score = candidate.score
    components.show_tiles(
        [
            ("Overall score", f"{score.overall:.1f}"),
            ("Must-haves", f"{score.must_have_pct:.0f}%"),
            ("Nice-to-haves", f"{score.nice_to_have_pct:.0f}%"),
        ],
        per_row=3,
    )
    components.show(f"<p>{components.band_pill(score.band)} {components.ai_note()}</p>")
    st.caption("Calculated by code (70% must-haves, 30% nice-to-haves; bands at 75 and 55), never by the AI.")
    if score.must_have_gaps:
        st.markdown("Must-have gaps: " + ", ".join(score.must_have_gaps))


def show_strengths_and_concerns(candidate: CandidateResult) -> None:
    if candidate.assessment is None:
        return
    columns = st.columns(2)
    with columns[0]:
        st.markdown("**Strengths**")
        for item in candidate.assessment.strengths or ["None listed"]:
            st.markdown(f"- {item}")
    with columns[1]:
        st.markdown("**Concerns**")
        for item in candidate.assessment.concerns or ["None listed"]:
            st.markdown(f"- {item}")


def show_flags_and_intake(candidate: CandidateResult) -> None:
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


def show_detail(run: RunResult, candidate: CandidateResult) -> None:
    components.show(brief_html(candidate))
    show_score_breakdown(candidate)
    show_strengths_and_concerns(candidate)
    st.markdown("#### Requirement checklist")
    st.caption("Each “met” or “partial” needs a quote that code found in the CV. Click a § chip to read the clause.")
    headers = ["Requirement", "Status", "Quote from the CV", "Verified", "Reasoning", "Guidelines"]
    components.show(components.html_table(headers, evidence_rows(run, candidate)))
    show_flags_and_intake(candidate)


# ===========================================================================
# Decision panel (FR-D1 to FR-D3)
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


def show_decision_panel(memory: Memory, run: RunResult, candidate: CandidateResult, decisions: dict) -> None:
    saved = decisions.get(candidate.candidate_id)
    with st.container(border=True):
        st.markdown(f"#### Your decision for {candidate_name(candidate)}")
        st.caption("The AI never shortlists or rejects anyone. Only your recorded decision does.")
        current = saved["decision"] if saved else "Pending"
        st.radio("Decision", ALLOWED_DECISIONS, index=ALLOWED_DECISIONS.index(current), horizontal=True,
                 key=f"decision_{candidate.candidate_id}")
        st.text_area(
            "Reason or notes (a rejection needs a job-related reason of at least 10 characters)",
            value=saved["reason"] if saved and saved["reason"] else "",
            key=f"reason_{candidate.candidate_id}",
            height=90,
        )
        if st.button("Save decision", type="primary", key=f"save_{candidate.candidate_id}"):
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


def show_email_draft(settings: Settings, memory: Memory, run: RunResult, candidate: CandidateResult) -> None:
    if not candidate.missing_info:
        return
    cid = candidate.candidate_id
    with st.container(border=True):
        st.markdown("#### Ask for the missing details")
        st.caption("Drafts an email asking for exactly what's missing. It is never sent automatically.")
        if st.button("Draft email", key=f"draft_email_{cid}"):
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
        if st.button("Reset decisions"):
            st.session_state["confirm_reset_decisions"] = True
            st.rerun()
        return
    st.warning("Clear every decision for this run? This is logged in the audit log.")
    columns = st.columns([1, 1, 4])
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


def render(settings: Settings, memory: Memory) -> None:
    st.header("4. Review")
    st.markdown(
        "Check the AI's evidence and record your decision for each candidate: **Shortlist**, **Hold** or "
        "**Reject**. The band is the AI's recommendation; the decision is yours."
    )
    run = state.current_run()
    candidates = screened_candidates(run) if run else []
    if not candidates:
        st.info("No screened candidates yet. Run the screening on page 3 first.")
        return

    decisions = memory.get_decisions(run.run_id)
    failed = [candidate for candidate in run.candidates if candidate.error]
    if failed:
        st.warning(f"{len(failed)} CV(s) couldn't be screened. Retry them on page 3.")

    show_summary(candidates, decisions)
    st.download_button("Download Excel", build_workbook(run, decisions), file_name="talentlens_screening.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       help="Ranking, comparison matrix and evidence, one sheet each")
    show_ranked_cards(candidates, decisions)
    show_matrix(run, candidates)

    st.divider()
    ids = [candidate.candidate_id for candidate in candidates]
    if st.session_state.get("selected_candidate") not in ids:
        st.session_state["selected_candidate"] = ids[0]
    names = {candidate.candidate_id: candidate_name(candidate) for candidate in candidates}
    selected_id = st.selectbox("Candidate details", ids, format_func=lambda cid: names[cid], key="selected_candidate")
    selected = next(candidate for candidate in candidates if candidate.candidate_id == selected_id)

    columns = st.columns([3, 2])
    with columns[0]:
        show_detail(run, selected)
    with columns[1]:
        show_decision_panel(memory, run, selected, decisions)
        show_email_draft(settings, memory, run, selected)
        st.divider()
        show_reset_decisions(memory, run)
