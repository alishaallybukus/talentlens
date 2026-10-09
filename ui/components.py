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
