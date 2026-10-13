"""Light and dark themes: the colours for Streamlit's own widgets and for our CSS, and the switch between them.

Why this file exists:
- Streamlit draws some things itself (tables, inputs, buttons), and we draw the
  rest with our own CSS (cards, chips, charts). Both must change together, or
  dark mode looks broken.
- Streamlit has no Python function to change the theme, so we change its theme
  settings while the app runs and redraw the page. That setting is shared by
  everyone using the app at the same moment, which is fine for a single
  recruiter (and for a demo).
- Dark mode has its own colours, picked for contrast on a dark background.
  It is not an automatic "invert".
"""

from __future__ import annotations

import streamlit as st

# Streamlit's own theme settings for each mode. The light values match .streamlit/config.toml.
STREAMLIT_LIGHT: dict[str, str] = {
    "theme.base": "light",
    "theme.backgroundColor": "#F6F7F5",
    "theme.secondaryBackgroundColor": "#EEF2F1",
    "theme.textColor": "#1B2430",
    "theme.primaryColor": "#0E7C7B",
    "theme.borderColor": "#DDE3E6",
    "theme.linkColor": "#0A5E5D",
    "theme.dataframeHeaderBackgroundColor": "#F1F5F4",
    "theme.greenColor": "#1E7F4F",
    "theme.redColor": "#B3261E",
    "theme.orangeColor": "#9A6700",
    "theme.blueColor": "#2B5FA6",
}
STREAMLIT_DARK: dict[str, str] = {
    "theme.base": "dark",
    "theme.backgroundColor": "#0E1316",
    "theme.secondaryBackgroundColor": "#1B2429",
    "theme.textColor": "#E7EDEF",
    "theme.primaryColor": "#2DB5A9",
    "theme.borderColor": "#2A363C",
    "theme.linkColor": "#6ED3C9",
    "theme.dataframeHeaderBackgroundColor": "#1A2328",
    "theme.greenColor": "#5CD39A",
    "theme.redColor": "#FF8A80",
    "theme.orangeColor": "#F0C05A",
    "theme.blueColor": "#8DB6FF",
}

# Our CSS variables in dark mode (the light values are at the top of ui/styles.css).
CSS_DARK: dict[str, str] = {
    "--tl-primary": "#2DB5A9",
    "--tl-primary-dark": "#6ED3C9",
    "--tl-primary-tint": "#10302D",
    "--tl-text": "#E7EDEF",
    "--tl-muted": "#A3B1B7",
    "--tl-background": "#0E1316",
    "--tl-surface": "#151C20",
    "--tl-border": "#2A363C",
    "--tl-met": "#5CD39A",       "--tl-met-bg": "#10301F",
    "--tl-partial": "#F0C05A",   "--tl-partial-bg": "#33280E",
    "--tl-missing": "#FF8A80",   "--tl-missing-bg": "#3B1715",
    "--tl-info": "#8DB6FF",      "--tl-info-bg": "#142338",
    "--tl-neutral": "#AEB9C1",   "--tl-neutral-bg": "#222C31",
    "--tl-divider": "#212B30",
    "--tl-table-head": "#1A2328",
    "--tl-hover": "#1A2327",
    "--tl-quote-bg": "#132427",
    "--tl-on-primary": "#04201D",
    "--tl-accent-possible": "#E0B14A",
    "--tl-accent-notmatch": "#75828C",
    "--tl-ring-track": "#2A363C",
    "--tl-shadow-sm": "0 1px 2px rgba(0, 0, 0, 0.35), 0 1px 3px rgba(0, 0, 0, 0.3)",
    "--tl-shadow-md": "0 6px 18px rgba(0, 0, 0, 0.45)",
}

# Colours for Altair charts, which can't read CSS variables. Checked with the dataviz palette
# validator against each mode's card surface (#FFFFFF and #151C20): lightness band, chroma,
# colour-blind separation and 3:1 contrast all pass.
CHART_LIGHT: dict[str, str] = {
    "text": "#1B2430", "muted": "#4A5563", "grid": "#EEF1F3", "rule": "#9AA4B0",
    "bar": "#00908A",                              # single-series bars
    "series_a": "#2A78D6", "series_b": "#EB6834",  # two series (e.g. Gemini, Ollama)
}
CHART_DARK: dict[str, str] = {
    "text": "#E7EDEF", "muted": "#A3B1B7", "grid": "#212B30", "rule": "#5E6B74",
    "bar": "#1FA195",
    "series_a": "#3A86DE", "series_b": "#D9672F",
}


def is_dark() -> bool:
    return st._config.get_option("theme.base") == "dark"


def request_toggle() -> None:
    """Button callback: ask for the other theme. sync() applies it on this run."""
    st.session_state["theme_wanted"] = "light" if is_dark() else "dark"


def apply(dark: bool) -> None:
    for key, value in (STREAMLIT_DARK if dark else STREAMLIT_LIGHT).items():
        st._config.set_option(key, value)


def sync() -> None:
    """Apply a requested theme change, then redraw so Streamlit sends the new theme to the browser."""
    wanted = st.session_state.pop("theme_wanted", None)
    if wanted is None or (wanted == "dark") == is_dark():
        return
    apply(wanted == "dark")
    st.rerun()


def css_overrides() -> str:
    """The dark values of our CSS variables, or nothing in light mode."""
    if not is_dark():
        return ""
    values = " ".join(f"{name}: {value};" for name, value in CSS_DARK.items())
    return f":root {{ {values} }}"


def chart_colors() -> dict[str, str]:
    return CHART_DARK if is_dark() else CHART_LIGHT
