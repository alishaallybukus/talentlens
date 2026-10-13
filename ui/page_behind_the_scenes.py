"""Page 6, Behind the scenes: what the agents did, which prompts they used, how well they score, and every human action.

Why this file exists (FR-O1 to FR-O5):
- Agentic systems should be inspectable. This page shows the full trace of a
  run (each agent step with its time, tokens, cache use and retrieved clauses),
  a summary per agent, the exact prompt templates with their versions, the
  latest evaluation results, and the audit log of human decisions.
"""

from __future__ import annotations

import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from core import prompts
from core.config import PROJECT_ROOT, Settings
from core.memory import Memory
from core.schemas import RunResult
from ui import charts, components, state, theme

EVAL_RESULTS_DIR: Path = PROJECT_ROOT / "eval" / "results"


# ===========================================================================
# Agent trace (FR-O1) and per-agent summary (FR-O2)
# ===========================================================================


def trace_table(run: RunResult) -> pd.DataFrame:
    rows = []
    for event in run.trace:
        rows.append(
            {
                "Time": state.short_time(event.ts),
                "Agent": event.agent,
                "Candidate": event.candidate or "",
                "Action": event.action,
                "Status": event.status,
                "ms": event.duration_ms,
                "Tokens in": event.tokens_in,
                "Tokens out": event.tokens_out,
                "Cache": "yes" if event.cache_hit else "",
                "Model": event.model or "",
                "Prompt": event.prompt_version or "",
                "Clauses": ", ".join(event.retrieved),
                "Detail": event.detail or "",
            }
        )
    return pd.DataFrame(rows)


def show_trace(run: RunResult) -> None:
    st.caption("Every step of the run, in order. AI steps show their tokens, cache use and the guideline clauses retrieved.")
    table = trace_table(run)
    candidates = sorted(name for name in table["Candidate"].unique() if name)
    chosen = st.selectbox("Show candidate", ["All candidates"] + candidates, key="trace_filter")
    if chosen != "All candidates":
        table = table[table["Candidate"] == chosen]
    st.dataframe(table, hide_index=True, width="stretch", height=480)


def agent_summary(run: RunResult) -> pd.DataFrame:
    """One row per agent: calls, cache hits, average time and tokens. Only finished AI steps count."""
    summary: dict[str, dict] = {}
    for event in run.trace:
        if event.status not in ("done", "revised") or event.prompt_version is None:
            continue
        row = summary.setdefault(event.agent, {"Calls": 0, "Cache hits": 0, "total_ms": 0,
                                               "Tokens in": 0, "Tokens out": 0})
        row["Calls"] += 1
        row["Cache hits"] += 1 if event.cache_hit else 0
        row["total_ms"] += event.duration_ms
        row["Tokens in"] += event.tokens_in
        row["Tokens out"] += event.tokens_out
    rows = []
    for agent, row in summary.items():
        average_seconds = row.pop("total_ms") / row["Calls"] / 1000
        rows.append({"Agent": agent, **row, "Average time (s)": round(average_seconds, 1)})
    return pd.DataFrame(rows)


def tokens_chart(table: pd.DataFrame) -> alt.LayerChart:
    """Horizontal bars: total tokens per agent. One measure, one hue, labelled ends, hover tooltip."""
    colors = theme.chart_colors()
    data = table.assign(Tokens=table["Tokens in"] + table["Tokens out"])
    base = alt.Chart(data).encode(
        y=alt.Y("Agent:N", sort="-x", title=None,
                axis=alt.Axis(labelFontSize=13, labelColor=colors["text"], ticks=False, domain=False)),
        x=alt.X("Tokens:Q", title=None, axis=alt.Axis(grid=True, gridColor=colors["grid"], labelColor=colors["muted"],
                                                       tickCount=4, domain=False, ticks=False, format="~s")),
    )
    bars = base.mark_bar(color=colors["bar"], cornerRadiusEnd=4, height=18).encode(
        tooltip=[alt.Tooltip("Agent:N"), alt.Tooltip("Calls:Q"), alt.Tooltip("Tokens in:Q", format=","),
                 alt.Tooltip("Tokens out:Q", format=","), alt.Tooltip("Tokens:Q", title="Total tokens", format=",")]
    )
    labels = base.mark_text(align="left", dx=6, color=colors["muted"], fontSize=12).encode(
        text=alt.Text("Tokens:Q", format=",")
    )
    return (bars + labels).properties(height=46 * len(data) + 10).configure_view(stroke=None).configure(background="transparent")


def show_agent_summary(run: RunResult) -> None:
    table = agent_summary(run)
    if table.empty:
        st.info("No AI steps in this run yet.")
        return
    st.markdown('<p class="tl-section-title">Tokens per agent</p>', unsafe_allow_html=True)
    st.altair_chart(tokens_chart(table), width="stretch")
    st.dataframe(table, hide_index=True, width="stretch")
    st.caption("Tokens are what each call used when it first ran. Cached steps re-use the saved answer: "
               "no new tokens, almost no time.")


# ===========================================================================
# Prompts (FR-O3)
# ===========================================================================


def show_prompts() -> None:
    st.caption("The exact instructions each agent gets. Markers like <<cv>> are filled with real data at run time.")
    with st.expander("Rules shared by every agent"):
        st.code(prompts.SHARED_RULES, language=None, wrap_lines=True)
    for template in prompts.ALL_PROMPTS:
        with st.expander(f"{template.name} · {template.version} · temperature {template.temperature}"):
            st.markdown("**System message**")
            st.code(template.system, language=None, wrap_lines=True)
            st.markdown("**User message template**")
            st.code(template.user, language=None, wrap_lines=True)


# ===========================================================================
# Evaluation (FR-O4): reads the files written by eval/run_eval.py
# ===========================================================================


def latest_results_by_provider() -> dict[str, dict]:
    """The newest eval_{provider}_{timestamp}.json for each provider."""
    latest: dict[str, dict] = {}
    if not EVAL_RESULTS_DIR.exists():
        return latest
    for path in sorted(EVAL_RESULTS_DIR.glob("eval_*.json")):  # timestamps sort oldest first
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        provider = data.get("provider", path.stem.split("_")[1])
        data["file"] = path.name
        latest[provider] = data
    return latest


SHORT_BANDS: dict[str, str] = {"Strong match": "Strong", "Possible match": "Possible", "Not a match for this role": "Not a match"}


def results_by_cv_html(results: dict[str, dict]) -> str:
    """One row per CV, one column per model: ✔ pass or ✖ with the reason on hover."""
    providers = list(results)
    cvs: dict[str, dict[str, dict]] = {}
    for provider in providers:
        for case in results[provider].get("cases", []):
            cvs.setdefault(case.get("name") or case.get("cv", "?"), {})[provider] = case
    headers = ["CV"] + [components.safe(provider.title()) for provider in providers]
    rows = []
    for name, by_provider in cvs.items():
        row = [f"<strong>{components.safe(name)}</strong>"]
        for provider in providers:
            case = by_provider.get(provider)
            if case is None:
                row.append('<span class="tl-muted">not run</span>')
            elif case.get("passed"):
                band = case.get("band", "")
                row.append(f'<span title="{components.safe(band)}">{components.chip("✔ " + SHORT_BANDS.get(band, band), "met")}</span>')
            else:
                row.append(f'<span title="{components.safe(case.get("problems", ""))}">'
                           f'{components.chip("✖ failed", "missing")}</span>')
        rows.append(row)
    return components.html_table(headers, rows)


def show_evaluation() -> None:
    results = latest_results_by_provider()
    if not results:
        st.info("No evaluation results yet. Run `python -m eval.run_eval --provider ollama` to create them.")
        return
    st.caption("The real pipeline on the 7 fictional demo CVs, compared with the expected results in "
               "data/sample/ground_truth.json (band, missing information, flags).")
    tiles = []
    for provider, data in results.items():
        summary = data.get("summary", {})
        tiles.append((f"{provider.title()}: CVs correct", summary.get("passed", "-"), "primary"))
        tiles.append((f"{provider.title()}: tokens per CV", f"{summary.get('avg_tokens_per_cv', 0):,}", ""))
    components.show_kpis(tiles)
    columns = st.columns([1.3, 1], gap="large")
    with columns[0], st.container(key="card-eval-chart"):
        components.show('<p class="tl-section-title">Accuracy by model</p>')
        st.altair_chart(charts.evaluation_chart(results), width="stretch")
        st.caption("Percentages from the latest evaluation of each model. Hover a bar for the exact value.")
    with columns[1], st.container(key="card-eval-cvs"):
        components.show('<p class="tl-section-title">Results by CV</p>')
        components.show(results_by_cv_html(results))
    for provider, data in results.items():
        with st.expander(f"All details: {provider.title()} · {data.get('model', '')} ({data['file']})"):
            cases = data.get("cases", [])
            if cases:
                st.dataframe(pd.DataFrame(cases), hide_index=True, width="stretch")


# ===========================================================================
# Audit log (FR-O5)
# ===========================================================================


def show_audit(memory: Memory, run: RunResult | None) -> None:
    only_this_run = st.toggle("Only this run", value=False, disabled=run is None, key="audit_this_run")
    rows = memory.list_audit(run_id=run.run_id if run and only_this_run else None)
    if not rows:
        st.info("No human actions recorded yet.")
        return
    table = pd.DataFrame(
        [
            {"Time": state.short_time(row["ts"]), "Who": row["actor"], "Event": row["event"],
             "Detail": row["detail"] or "", "Run": row["run_id"] or ""}
            for row in rows
        ]
    )
    st.dataframe(table, hide_index=True, width="stretch", height=420)
    st.caption("Every approval, decision, preference change and report approval, newest first.")


# ===========================================================================
# The page
# ===========================================================================


def render(settings: Settings, memory: Memory) -> None:
    components.page_header(
        "Step 6 of 6 · Observability",
        "Behind the scenes",
        "See exactly what the agents did, the prompts they used, how accurate they are, and every human action.",
    )
    run = state.current_run()
    tabs = st.tabs(["Agent trace", "Per-agent summary", "Prompts", "Evaluation", "Audit log"])
    with tabs[0]:
        if run is None or not run.trace:
            st.info("No screening yet. The trace appears after you run the screening in step 3.")
        else:
            show_trace(run)
    with tabs[1]:
        if run is None:
            st.info("No screening yet.")
        else:
            show_agent_summary(run)
    with tabs[2]:
        show_prompts()
    with tabs[3]:
        show_evaluation()
    with tabs[4]:
        show_audit(memory, run)
