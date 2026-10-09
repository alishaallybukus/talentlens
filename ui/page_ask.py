"""Ask the CVs (extra, FR-X1): a chat where the recruiter asks questions across all screened CVs.

Why this file exists:
- It shows agentic tool use in action: each answer lists the tools the
  assistant called (e.g. "🔧 search_cvs('TikTok')") and what they returned.
- Quotes in the answer are verified against the CVs, with a badge for each.
"""

from __future__ import annotations

import streamlit as st

from core.assistant import AssistantAnswer, CVTools, answer_question
from core.config import Settings
from core.memory import Memory
from core.report import screened_candidates
from ui import components, state

EXAMPLE_QUESTIONS = [
    "Who has TikTok experience?",
    "Who manages an ads budget above MUR 100,000?",
    "Which candidates are missing their notice period?",
]


def build_tools(memory: Memory) -> CVTools | None:
    run = state.current_run()
    if run is None:
        return None
    texts_by_hash = memory.cv_texts_by_hash()
    cv_texts = {}
    for content_hash, text in texts_by_hash.items():
        cv_texts[content_hash[:12]] = text  # candidate ids are the first 12 characters of the hash
    return CVTools(screened_candidates(run), cv_texts)


def citation_html(citation) -> str:
    badge = components.chip("✔ Quote verified", "met") if citation.verified else components.chip("✖ Not found in the CV", "missing")
    return (f"<div><strong>{components.safe(citation.candidate)}</strong>: "
            f"<span class='tl-quote'>“{components.safe(citation.quote)}”</span> {badge}</div>")


def show_answer(answer: AssistantAnswer) -> None:
    for call in answer.tool_calls:
        with st.expander(call.label()):
            st.text(call.result)
    st.markdown(answer.answer)
    if answer.citations:
        components.show("".join(citation_html(citation) for citation in answer.citations))


def ask(settings: Settings, memory: Memory, tools: CVTools, question: str) -> None:
    history = st.session_state.setdefault("ask_history", [])
    llm = state.make_llm(settings, memory)
    try:
        with st.spinner("Looking through the CVs..."):
            answer = answer_question(llm, question, tools)
    except Exception as error:  # never a raw error
        st.error(state.friendly_error(error))
        return
    history.append({"question": question, "answer": answer})


def render(settings: Settings, memory: Memory) -> None:
    st.header("Ask the CVs")
    st.markdown(
        "Ask a question across all screened CVs. The assistant looks things up with tools and quotes the CVs; "
        "every quote is checked. It never ranks or decides: that's your job."
    )
    tools = build_tools(memory)
    if tools is None or not tools.candidates:
        st.info("No screened candidates yet. Run the screening on page 3 first.")
        return

    columns = st.columns(len(EXAMPLE_QUESTIONS))
    clicked = None
    for column, example in zip(columns, EXAMPLE_QUESTIONS):
        if column.button(example, width="stretch"):
            clicked = example
    typed = st.chat_input("Ask about the candidates, e.g. Who has worked in retail?")
    question = typed or clicked
    if question:
        ask(settings, memory, tools, question)

    for turn in reversed(st.session_state.get("ask_history", [])):  # newest first
        with st.chat_message("user"):
            st.markdown(turn["question"])
        with st.chat_message("assistant"):
            show_answer(turn["answer"])
