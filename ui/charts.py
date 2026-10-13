"""The charts on the Home and Behind-the-scenes pages, drawn with Altair (it comes with Streamlit).

Why this file exists:
- One place for chart styling: thin bars with rounded ends, faint gridlines,
  values written at the bar ends, and a tooltip on hover.
- Colours come from ui/theme.py, so the charts follow light and dark mode.
  They were checked with a palette validator (contrast, colour-blind safety).
- A single measure uses a single colour. Bands are shown with labelled lines at
  55 and 75 instead of extra colours, so nothing relies on colour alone.
"""

from __future__ import annotations

import altair as alt
import pandas as pd

from core.report import candidate_name, decision_for
from core.schemas import CandidateResult, RunResult
from ui import theme

ROW_HEIGHT = 30


def finish(chart, height: int):
    """Shared look: transparent background, no outer box, theme-coloured text."""
    colors = theme.chart_colors()
    return (
        chart.properties(height=height)
        .configure(background="transparent")
        .configure_view(stroke=None)
        .configure_axis(labelColor=colors["muted"], titleColor=colors["muted"], labelFontSize=12)
        .configure_legend(labelColor=colors["text"], titleColor=colors["muted"], orient="top", labelFontSize=12)
    )


def y_axis(colors: dict[str, str]) -> alt.Axis:
    return alt.Axis(labelColor=colors["text"], labelFontSize=12, ticks=False, domain=False, labelLimit=260)


def x_axis(colors: dict[str, str], fmt: str = "d") -> alt.Axis:
    return alt.Axis(grid=True, gridColor=colors["grid"], domain=False, ticks=False, tickCount=5, format=fmt)


# ===========================================================================
# Scores, with the band thresholds as labelled lines
# ===========================================================================


def score_chart(candidates: list[CandidateResult], decisions: dict[str, dict]):
    colors = theme.chart_colors()
    data = pd.DataFrame(
        [
            {
                "Candidate": candidate_name(candidate),
                "Score": candidate.score.overall,
                "Band": candidate.score.band,
                "Decision": decision_for(candidate, decisions),
            }
            for candidate in candidates
        ]
    )
    order = list(data.sort_values("Score", ascending=False)["Candidate"])
    base = alt.Chart(data).encode(
        y=alt.Y("Candidate:N", sort=order, title=None, axis=y_axis(colors)),
        x=alt.X("Score:Q", title=None, scale=alt.Scale(domain=[0, 100]), axis=x_axis(colors)),
    )
    bars = base.mark_bar(color=colors["bar"], cornerRadiusEnd=4, height=16).encode(
        tooltip=["Candidate:N", alt.Tooltip("Score:Q", format=".1f"), "Band:N", "Decision:N"]
    )
    values = base.mark_text(align="left", dx=6, color=colors["muted"], fontSize=12).encode(
        text=alt.Text("Score:Q", format=".0f")
    )
    thresholds = pd.DataFrame({"Score": [55, 75], "Label": ["Possible ≥ 55", "Strong ≥ 75"]})
    rules = alt.Chart(thresholds).mark_rule(color=colors["rule"], strokeDash=[4, 4]).encode(x="Score:Q")
    rule_labels = alt.Chart(thresholds).mark_text(
        align="left", dx=4, dy=-6, baseline="bottom", color=colors["muted"], fontSize=11
    ).encode(x="Score:Q", y=alt.value(0), text="Label:N")
    return finish(rules + bars + values + rule_labels, height=ROW_HEIGHT * len(data) + 24)


# ===========================================================================
# Requirement coverage: which requirements candidates most often lack
# ===========================================================================


def coverage_frame(run: RunResult, candidates: list[CandidateResult]) -> pd.DataFrame:
    rows = []
    for requirement in run.requirements.must_have + run.requirements.nice_to_have:
        counts = {"met": 0, "partial": 0, "missing": 0}
        for candidate in candidates:
            statuses = {result.requirement_id: result.status for result in candidate.assessment.results}
            counts[statuses.get(requirement.id, "missing")] += 1
        total = max(len(candidates), 1)
        rows.append(
            {
                "Requirement": f"{requirement.id} {requirement.label}",
                "Coverage": round((counts["met"] + 0.5 * counts["partial"]) / total * 100),
                "Met": counts["met"],
                "Partial": counts["partial"],
                "Missing": counts["missing"],
            }
        )
    return pd.DataFrame(rows)


def coverage_chart(run: RunResult, candidates: list[CandidateResult]):
    colors = theme.chart_colors()
    data = coverage_frame(run, candidates)
    base = alt.Chart(data).encode(
        y=alt.Y("Requirement:N", sort=list(data["Requirement"]), title=None, axis=y_axis(colors)),
        x=alt.X("Coverage:Q", title=None, scale=alt.Scale(domain=[0, 100]), axis=x_axis(colors)),
    )
    bars = base.mark_bar(color=colors["bar"], cornerRadiusEnd=4, height=12).encode(
        tooltip=["Requirement:N", alt.Tooltip("Coverage:Q", title="Coverage (%)"), "Met:Q", "Partial:Q", "Missing:Q"]
    )
    values = base.mark_text(align="left", dx=6, color=colors["muted"], fontSize=11).encode(
        text=alt.Text("Coverage:Q", format="d")
    )
    return finish(bars + values, height=24 * len(data) + 10)


# ===========================================================================
# Evaluation: Gemini and Ollama side by side
# ===========================================================================

EVAL_METRICS: list[tuple[str, str]] = [
    ("band_accuracy_pct", "Band accuracy"),
    ("missing_info_precision_pct", "Missing info: precision"),
    ("missing_info_recall_pct", "Missing info: recall"),
    ("quotes_verified_first_try_pct", "Quotes verified first try"),
]


def evaluation_frame(results: dict[str, dict]) -> pd.DataFrame:
    rows = []
    for provider, data in results.items():
        summary = data.get("summary", {})
        for key, label in EVAL_METRICS:
            if key in summary:
                rows.append({"Metric": label, "Model": f"{provider.title()} ({data.get('model', '')})",
                             "Percent": float(summary[key])})
    return pd.DataFrame(rows)


def evaluation_chart(results: dict[str, dict]):
    """Grouped bars: each metric, one bar per model. Two series: legend plus labels on every bar."""
    colors = theme.chart_colors()
    data = evaluation_frame(results)
    models = sorted(data["Model"].unique())
    palette = [colors["series_a"], colors["series_b"]][: len(models)]
    base = alt.Chart(data).encode(
        y=alt.Y("Metric:N", title=None, sort=[label for _key, label in EVAL_METRICS], axis=y_axis(colors),
                scale=alt.Scale(paddingInner=0.35)),
        yOffset=alt.YOffset("Model:N", sort=models, scale=alt.Scale(paddingInner=0.15)),
        x=alt.X("Percent:Q", title=None, scale=alt.Scale(domain=[0, 100]), axis=x_axis(colors)),
    )
    bars = base.mark_bar(cornerRadiusEnd=4).encode(
        color=alt.Color("Model:N", sort=models, scale=alt.Scale(domain=models, range=palette), title=None),
        tooltip=["Metric:N", "Model:N", alt.Tooltip("Percent:Q", format=".1f")],
    )
    values = base.mark_text(align="left", dx=5, color=colors["muted"], fontSize=11).encode(
        text=alt.Text("Percent:Q", format=".0f")
    )
    # A height "step" sizes each bar inside its group, so grouped bars never overlap.
    return finish(bars + values, height=alt.Step(17))
