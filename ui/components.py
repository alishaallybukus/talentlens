"""Reusable visual pieces for the TalentLens app: chips, pills, tiles, the score ring, timeline steps.

Why this file exists:
- Every page shows the same kinds of things (a band, a status, a statistic).
  Building them in one place keeps the look consistent (spec 12.3).
- Each function returns a small piece of HTML. Any text that comes from a CV
  or from the AI is escaped first, so it can never inject HTML into the page.
- Status is always shown with a symbol AND a word, never by colour alone.
"""

from __future__ import annotations

import html
from pathlib import Path

import streamlit as st

STYLES_PATH = Path(__file__).with_name("styles.css")

STATUS_SYMBOLS: dict[str, tuple[str, str]] = {
    "met": ("✔", "Met"),
    "partial": ("◐", "Partial"),
    "missing": ("✖", "Missing"),
}

BAND_CLASSES: dict[str, str] = {
    "Strong match": "tl-band-strong",
    "Possible match": "tl-band-possible",
    "Not a match for this role": "tl-band-notmatch",
}

STEP_SYMBOLS: dict[str, str] = {
    "waiting": "○",
    "running": "◔",
    "done": "✔",
    "revised": "↻",
    "error": "✖",
}


def safe(text: object) -> str:
    """Escape text for HTML, so CV or AI text can't change the page."""
    return html.escape(str(text), quote=True)


def load_styles() -> None:
    """Add styles.css to the page. Called once at the top of every run of the app."""
    css = STYLES_PATH.read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def show(html_text: str) -> None:
    """Draw a piece of HTML built by the functions below."""
    st.markdown(html_text, unsafe_allow_html=True)


# --- Chips and pills ----------------------------------------------------------


def chip(text: str, kind: str = "neutral") -> str:
    """A small rounded label. kind: met, partial, missing, info, neutral or primary."""
    return f'<span class="tl-chip tl-{kind}">{safe(text)}</span>'


def status_chip(status: str) -> str:
    """✔ Met, ◐ Partial or ✖ Missing."""
    symbol, word = STATUS_SYMBOLS.get(status, ("?", status))
    return f'<span class="tl-chip tl-{safe(status)}">{symbol} {word}</span>'


def band_pill(band: str) -> str:
    """The AI's recommendation band. "Not a match" is grey, never red."""
    css_class = BAND_CLASSES.get(band, "tl-neutral")
    return f'<span class="tl-chip {css_class}">{safe(band)}</span>'


def clause_chip(clause_id: str, clause_text: str | None) -> str:
    """A guideline citation like §4.4 that expands to show the clause text."""
    body = safe(clause_text) if clause_text else "Clause text not available."
    return f'<details class="tl-clause"><summary>{safe(clause_id)}</summary><div>{body}</div></details>'


def intake_badges(word_count: int, redaction_count: int, quarantined_count: int) -> str:
    """The badges shown on each loaded CV (FR-C3)."""
    badges = [chip(f"{word_count} words", "neutral")]
    if redaction_count:
        badges.append(chip(f"🛡 {redaction_count} details redacted", "info"))
    if quarantined_count:
        badges.append(chip("⚠ Suspicious text removed", "missing"))
    return " ".join(badges)


# --- Tiles and score ring -------------------------------------------------------


def stat_tile(label: str, value: object) -> str:
    return (
        f'<div class="tl-tile"><div class="tl-tile-value">{safe(value)}</div>'
        f'<div class="tl-tile-label">{safe(label)}</div></div>'
    )


def show_tiles(tiles: list[tuple[str, object]], per_row: int = 5) -> None:
    """Draw statistic tiles in rows of `per_row`."""
    for start in range(0, len(tiles), per_row):
        row = tiles[start : start + per_row]
        columns = st.columns(per_row)
        for column, (label, value) in zip(columns, row):
            with column:
                show(stat_tile(label, value))


def score_ring(score: float, size: int = 64) -> str:
    """A circular gauge (SVG) showing a score from 0 to 100."""
    radius = 26
    circumference = 2 * 3.14159 * radius
    filled = max(0.0, min(score, 100.0)) / 100 * circumference
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 64 64" role="img" aria-label="Score {score:.1f}">'
        f'<circle cx="32" cy="32" r="{radius}" fill="none" stroke="#DDE3E6" stroke-width="6"/>'
        f'<circle cx="32" cy="32" r="{radius}" fill="none" stroke="#0E7C7B" stroke-width="6" '
        f'stroke-dasharray="{filled:.1f} {circumference:.1f}" transform="rotate(-90 32 32)" stroke-linecap="round"/>'
        f'<text x="32" y="37" text-anchor="middle" font-size="15" font-weight="700" fill="#1B2430">{score:.0f}</text>'
        "</svg>"
    )


# --- Timeline -------------------------------------------------------------------


def step_chip(step_name: str, status: str, duration_ms: int = 0) -> str:
    """One step of the live timeline, e.g. "✔ CV Analyst 4.2s"."""
    symbol = STEP_SYMBOLS.get(status, "○")
    timing = f" {duration_ms / 1000:.1f}s" if duration_ms else ""
    return f'<span class="tl-step tl-step-{safe(status)}">{symbol} {safe(step_name)}{timing}</span>'


def timeline_row(name: str, steps: list[str], note: str = "") -> str:
    note_html = f'<span class="tl-muted">{safe(note)}</span>' if note else ""
    return f'<div class="tl-timeline-row"><span class="tl-timeline-name">{safe(name)}</span>{"".join(steps)}{note_html}</div>'


def status_line(text: str, kind: str = "primary") -> str:
    """A highlighted one-line status, e.g. "Approved by Alisha at 10:42"."""
    return f"<p>{chip(text, kind)}</p>"


# --- Review page pieces ---------------------------------------------------------

# A small icon for each flag code (spec 6), always shown with the message as a tooltip.
FLAG_ICONS: dict[str, str] = {
    "missing_info": "❓",
    "salary_above_band": "💰",
    "salary_not_comparable": "💱",
    "outside_mauritius": "🌍",
    "career_gap": "⏸",
    "protected_info_redacted": "🛡",
    "prompt_injection": "⚠",
    "unverified_evidence": "🔎",
    "bias_filtered": "⚖",
    "references_later": "📄",
}

SEVERITY_KINDS: dict[str, str] = {"info": "info", "warning": "partial", "critical": "missing"}

DECISION_KINDS: dict[str, str] = {
    "Pending": "neutral",
    "Shortlist": "met",
    "Hold": "partial",
    "Reject": "missing",
}


def flag_icons(flags) -> str:
    """One icon per flag, with the flag's message on hover."""
    icons = []
    for flag in flags:
        icon = FLAG_ICONS.get(flag.code, "•")
        icons.append(f'<span class="tl-flag-icon" title="{safe(flag.message)}">{icon}</span>')
    return "".join(icons)


def flag_chip(flag) -> str:
    """A flag as a chip: icon, message and the guideline clause, coloured by severity."""
    kind = SEVERITY_KINDS.get(flag.severity, "neutral")
    guideline = f" ({flag.guideline})" if flag.guideline else ""
    return chip(f"{FLAG_ICONS.get(flag.code, '•')} {flag.message}{guideline}", kind)


def decision_chip(decision: str) -> str:
    """The recruiter's decision. It's the human's call, so it's shown apart from the AI band."""
    label = "Decision: Pending" if decision == "Pending" else f"Decision: {decision}"
    return chip(label, DECISION_KINDS.get(decision, "neutral"))


def ai_note() -> str:
    """Microcopy shown next to every band (FR-R6)."""
    return '<span class="tl-ai-note">AI recommendation, not a decision</span>'


def html_table(headers: list[str], rows: list[list[str]], css_class: str = "tl-table") -> str:
    """A simple table. Cells must already be safe HTML (use safe() or the chip functions)."""
    head = "".join(f"<th>{header}</th>" for header in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f'<div class="tl-table-wrap"><table class="{css_class}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


# --- Logo -------------------------------------------------------------------------


def logo_html(app_name: str, tagline: str) -> str:
    """The TalentLens logo: a teal lens over a document, with the name and tagline."""
    svg = (
        '<svg width="40" height="40" viewBox="0 0 40 40" role="img" aria-label="TalentLens logo">'
        '<rect x="4" y="3" width="22" height="28" rx="3" fill="#E6F3F2" stroke="#0A5E5D" stroke-width="2"/>'
        '<line x1="9" y1="10" x2="21" y2="10" stroke="#0A5E5D" stroke-width="2" stroke-linecap="round"/>'
        '<line x1="9" y1="15" x2="18" y2="15" stroke="#0A5E5D" stroke-width="2" stroke-linecap="round"/>'
        '<circle cx="24" cy="23" r="8" fill="#FFFFFF" stroke="#0E7C7B" stroke-width="3"/>'
        '<path d="M20.5 23.2l2.4 2.4 4.6-4.8" fill="none" stroke="#1E7F4F" stroke-width="2.2" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        '<line x1="30" y1="29" x2="36" y2="35" stroke="#0E7C7B" stroke-width="3.5" stroke-linecap="round"/>'
        "</svg>"
    )
    return (
        f'<div class="tl-logo-row">{svg}<div><p class="tl-logo">{safe(app_name)}</p>'
        f'<p class="tl-tagline">{safe(tagline)}</p></div></div>'
    )
