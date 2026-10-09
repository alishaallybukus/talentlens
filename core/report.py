"""The shortlist report for the hiring manager: who goes in, what each section says, and the downloads.

Why this file exists (spec 5.6, FR-P1 to FR-P5):
- This is human Checkpoint 3. Only candidates the RECRUITER shortlisted go in the
  report. Candidates on hold, rejected or still pending appear only as counts.
- The AI (Report Agent, shortlist mode) writes just the overview at the top.
  Everything else (one section per candidate, in the brief's format) is built
  here by code from data we already checked, so the AI can't add new facts.
- The approved report is downloaded as .md and as .html. The HTML is built by a
  small converter below that understands only the Markdown this file writes,
  so no extra library is needed. All text is escaped, so it can't inject HTML.
"""

from __future__ import annotations

import html
import re

from core.guardrails import MISSING_INFO_NAMES
from core.schemas import CandidateResult, RunResult, ShortlistOverview

DECISION_ORDER: list[str] = ["Shortlist", "Hold", "Reject", "Pending"]
FOOTER_START = "Prepared with AI assistance."


# ===========================================================================
# Who is in the report
# ===========================================================================


def screened_candidates(run: RunResult) -> list[CandidateResult]:
    """Candidates that finished screening (CVs with an error can't be decided on yet)."""
    return [candidate for candidate in run.candidates if candidate.error is None and candidate.score is not None]


def decision_for(candidate: CandidateResult, decisions: dict[str, dict]) -> str:
    """The recruiter's decision for a candidate. No saved decision means Pending."""
    saved = decisions.get(candidate.candidate_id)
    return saved["decision"] if saved else "Pending"


def count_decisions(run: RunResult, decisions: dict[str, dict]) -> dict[str, int]:
    """How many candidates are shortlisted, on hold, rejected and pending."""
    counts = {name: 0 for name in DECISION_ORDER}
    for candidate in screened_candidates(run):
        counts[decision_for(candidate, decisions)] += 1
    return counts


def shortlisted_candidates(run: RunResult, decisions: dict[str, dict]) -> list[CandidateResult]:
    """ONLY the candidates the recruiter shortlisted, in ranking order (FR-P5)."""
    return [
        candidate for candidate in screened_candidates(run) if decision_for(candidate, decisions) == "Shortlist"
    ]


def candidate_name(candidate: CandidateResult) -> str:
    if candidate.profile and candidate.profile.name:
        return candidate.profile.name
    return candidate.file_name


def missing_info_names(candidate: CandidateResult) -> list[str]:
    """['salary_expectation'] -> ['salary expectation']."""
    return [MISSING_INFO_NAMES.get(key, key) for key in candidate.missing_info]


def recruiter_notes(candidate: CandidateResult, decisions: dict[str, dict]) -> str:
    saved = decisions.get(candidate.candidate_id)
    return (saved.get("reason") or "").strip() if saved else ""


# ===========================================================================
# What the Report Agent is given (shortlisted candidates only)
# ===========================================================================


def overview_input(run: RunResult, decisions: dict[str, dict]) -> list[dict]:
    """The data for the shortlist overview prompt. Nobody who isn't shortlisted is included."""
    rows = []
    for candidate in shortlisted_candidates(run, decisions):
        rows.append(
            {
                "name": candidate_name(candidate),
                "band": candidate.score.band,
                "score": candidate.score.overall,
                "summary": candidate.report.summary if candidate.report else "",
                "strengths": candidate.assessment.strengths if candidate.assessment else [],
                "missing_information": missing_info_names(candidate),
                "recruiter_notes": recruiter_notes(candidate, decisions),
            }
        )
    return rows


# ===========================================================================
# Building the Markdown
# ===========================================================================


def footer_line(reviewer: str, date_text: str) -> str:
    """The line every report ends with (FR-P4)."""
    return f"{FOOTER_START} All decisions made by {reviewer} on {date_text}."


def bullet_list(items: list[str], empty_text: str) -> list[str]:
    if not items:
        return [f"- {empty_text}"]
    return [f"- {item}" for item in items]


def candidate_section(candidate: CandidateResult, decisions: dict[str, dict]) -> str:
    """One shortlisted candidate in the brief's format, built by code from checked data."""
    profile = candidate.profile
    report = candidate.report
    lines = [f"## {candidate_name(candidate)}", ""]

    # Candidate
    lines.append("**Candidate**")
    if profile and profile.headline:
        lines.append(f"- {profile.headline}")
    if profile and profile.location:
        lines.append(f"- Location: {profile.location}")
    lines.append(f"- AI score {candidate.score.overall:.0f} ({candidate.score.band}). AI recommendation, not a decision")
    if report and report.summary:
        lines += ["", report.summary]
    lines.append("")

    lines.append("**Relevant Experience**")
    lines.append(f"- {report.relevant_experience}" if report and report.relevant_experience else "- Not stated")
    lines.append("")

    lines.append("**Key Skills**")
    lines += bullet_list(report.key_skills if report else [], "None listed")
    lines.append("")

    lines.append("**Missing Information**")
    lines += bullet_list(missing_info_names(candidate), "Nothing missing")
    lines.append("")

    lines.append("**Questions for Interview**")
    questions = [question.question for question in report.interview_questions] if report else []
    lines += [f"{number}. {question}" for number, question in enumerate(questions, start=1)] or ["- None"]
    lines.append("")

    notes = recruiter_notes(candidate, decisions)
    if notes:
        lines += ["**Recruiter notes**", notes, ""]
    return "\n".join(lines)


def counts_line(counts: dict[str, int]) -> str:
    return (
        f"Shortlisted: {counts['Shortlist']} · On hold: {counts['Hold']} · "
        f"Rejected: {counts['Reject']} · Pending: {counts['Pending']}"
    )


def build_report_markdown(
    run: RunResult,
    decisions: dict[str, dict],
    overview: ShortlistOverview | None,
    reviewer: str,
    date_text: str,
) -> str:
    """The full draft report. Only shortlisted candidates get a section; the rest are counts."""
    title = run.requirements.title
    lines = [f"# Shortlist: {title}", ""]
    if run.requirements.company:
        lines += [f"{run.requirements.company} · {run.requirements.location or ''}".strip(" ·"), ""]
    lines += [counts_line(count_decisions(run, decisions)), ""]

    lines += ["## Overview", ""]
    if overview is not None:
        lines += [overview.overview, ""]
        if overview.points_to_discuss:
            lines += ["**Points to discuss**"] + [f"- {point}" for point in overview.points_to_discuss] + [""]
    else:
        lines += ["(No overview yet.)", ""]

    for candidate in shortlisted_candidates(run, decisions):
        lines.append(candidate_section(candidate, decisions))

    lines += ["---", "", footer_line(reviewer, date_text), ""]
    return "\n".join(lines)


def with_footer(markdown_text: str, reviewer: str, date_text: str) -> str:
    """Make sure the report ends with the footer naming who approved it and when.

    An old footer line (e.g. from the draft, or edited by hand) is replaced.
    """
    kept = [line for line in markdown_text.rstrip().splitlines() if not line.startswith(FOOTER_START)]
    while kept and kept[-1].strip() in ("", "---"):
        kept.pop()
    return "\n".join(kept + ["", "---", "", footer_line(reviewer, date_text), ""])


# ===========================================================================
# Markdown -> HTML (only what our reports use)
# ===========================================================================


def inline_html(text: str) -> str:
    """Escape the text, then turn **bold** into <strong>."""
    escaped = html.escape(text, quote=False)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)


def markdown_to_html_body(markdown_text: str) -> str:
    """Headings (#, ##, ###), bullet and numbered lists, rules (---) and paragraphs."""
    parts: list[str] = []
    open_list: str | None = None  # "ul" or "ol" while we're inside a list

    def close_list() -> None:
        nonlocal open_list
        if open_list:
            parts.append(f"</{open_list}>")
            open_list = None

    for raw_line in markdown_text.splitlines():
        line = raw_line.strip()
        heading = re.match(r"^(#{1,3})\s+(.*)$", line)
        bullet = re.match(r"^[-*]\s+(.*)$", line)
        numbered = re.match(r"^\d+\.\s+(.*)$", line)
        if heading:
            close_list()
            level = len(heading.group(1))
            parts.append(f"<h{level}>{inline_html(heading.group(2))}</h{level}>")
        elif line == "---":
            close_list()
            parts.append("<hr>")
        elif bullet or numbered:
            wanted = "ul" if bullet else "ol"
            if open_list != wanted:
                close_list()
                parts.append(f"<{wanted}>")
                open_list = wanted
            item = (bullet or numbered).group(1)
            parts.append(f"<li>{inline_html(item)}</li>")
        elif line == "":
            close_list()
        else:
            close_list()
            parts.append(f"<p>{inline_html(line)}</p>")
    close_list()
    return "\n".join(parts)


HTML_STYLE = """
body { font-family: Inter, "Segoe UI", system-ui, sans-serif; color: #1B2430; max-width: 780px;
       margin: 32px auto; padding: 0 20px; line-height: 1.5; font-size: 16px; }
h1 { color: #0A5E5D; font-size: 1.7rem; }
h2 { color: #0A5E5D; font-size: 1.25rem; border-bottom: 1px solid #DDE3E6; padding-bottom: 4px; margin-top: 28px; }
hr { border: none; border-top: 1px solid #DDE3E6; margin: 24px 0; }
p:last-of-type { color: #4A5563; }
@media print { body { margin: 0; } h2 { page-break-after: avoid; } }
"""


def markdown_to_html_page(markdown_text: str, title: str) -> str:
    """A complete HTML page that prints cleanly to PDF from the browser."""
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        f"<title>{html.escape(title)}</title>\n<style>{HTML_STYLE}</style>\n</head>\n<body>\n"
        f"{markdown_to_html_body(markdown_text)}\n</body>\n</html>\n"
    )
