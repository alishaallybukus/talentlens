"""Ask the CVs (extra, FR-X1, spec 5.8): a tool-calling assistant that answers questions across all CVs.

Why this file exists:
- The recruiter can ask things like "Who has TikTok experience?". The model
  can't see the CVs directly; it must call tools to look things up, one step at
  a time, then answer. This is the agent "tool use" loop, built on the same JSON
  layer as the other agents, so it works with Gemini and Ollama.
- Each step the model returns either {"action": "call_tool", ...} or
  {"action": "answer", ...}. It may call at most MAX_TOOL_CALLS tools.
- Every quote it cites is checked against that candidate's (redacted) CV text,
  exactly like the comparison evidence. The CV text the tools return is the
  cleaned text: protected details are already removed and injection lines quarantined.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from core import guardrails, prompts
from core.rag import Clause, GuidelineIndex
from core.schemas import AssistantStep, CandidateResult, Citation

MAX_TOOL_CALLS = 4
SEARCH_RESULTS = 6
NOT_FOUND_ANSWER = "I couldn't find that in the CVs."
TOOL_NAMES = ["search_cvs", "get_candidate", "list_candidates"]


@dataclass
class ToolCall:
    """One tool call, shown in the chat as "🔧 search_cvs('TikTok')"."""

    tool: str
    arguments: dict
    result: str

    def label(self) -> str:
        shown = ", ".join(repr(value) for value in self.arguments.values())
        return f"🔧 {self.tool}({shown})"


@dataclass
class AssistantAnswer:
    answer: str
    citations: list[Citation] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)


# ===========================================================================
# The tools
# ===========================================================================


class CVTools:
    """The three tools, over the screened candidates and their cleaned CV text."""

    def __init__(self, candidates: list[CandidateResult], cv_texts: dict[str, str]) -> None:
        """cv_texts maps candidate_id to the cleaned CV text."""
        self.candidates = [candidate for candidate in candidates if candidate.profile is not None]
        self.cv_texts = cv_texts
        self.index = GuidelineIndex(self.build_chunks())

    def build_chunks(self) -> list[Clause]:
        """One searchable chunk per non-empty CV line, labelled with the candidate's name."""
        chunks = []
        for candidate in self.candidates:
            text = self.cv_texts.get(candidate.candidate_id, "")
            for number, line in enumerate(text.splitlines()):
                if len(line.split()) >= 2:
                    chunks.append(Clause(id=f"{candidate.candidate_id}#{number}", section_title=candidate.profile.name,
                                         text=line.strip()))
        return chunks

    def find(self, name: str) -> CandidateResult | None:
        """A candidate by full or partial name, ignoring case."""
        wanted = name.strip().lower()
        for candidate in self.candidates:
            if candidate.profile.name.lower() == wanted:
                return candidate
        for candidate in self.candidates:
            if wanted and (wanted in candidate.profile.name.lower() or candidate.profile.name.lower().split()[0] == wanted):
                return candidate
        return None

    def search_cvs(self, query: str = "") -> str:
        hits = self.index.search(str(query), top_k=SEARCH_RESULTS)
        if not hits:
            return "No matching text in any CV."
        return "\n".join(f"- {hit.section_title}: \"{hit.text}\"" for hit in hits)

    def get_candidate(self, name: str = "") -> str:
        candidate = self.find(str(name))
        if candidate is None:
            return f"No candidate called '{name}'. Use list_candidates to see the names."
        profile = candidate.profile
        details = {
            "name": profile.name,
            "headline": profile.headline,
            "location": profile.location,
            "relevant_years_experience": profile.relevant_years_experience,
            "skills": profile.skills,
            "salary_expectation": profile.salary_expectation,
            "notice_period": profile.notice_period,
            "right_to_work": profile.right_to_work,
            "missing_information": candidate.missing_info,
            "score": candidate.score.overall if candidate.score else None,
            "band (AI recommendation)": candidate.score.band if candidate.score else None,
            "strengths": candidate.assessment.strengths if candidate.assessment else [],
            "summary": candidate.report.summary if candidate.report else None,
        }
        return json.dumps(details, ensure_ascii=False)

    def list_candidates(self, band: str | None = None) -> str:
        rows = []
        for candidate in self.candidates:
            if candidate.score is None:
                continue
            if band and candidate.score.band.lower() != str(band).lower():
                continue
            # Each required detail spelled out as given or MISSING: easy for a small model to read correctly.
            details = "; ".join(
                f"{name}: {'MISSING' if key in candidate.missing_info else 'given'}"
                for key, name in guardrails.MISSING_INFO_NAMES.items()
            )
            rows.append(f"- {candidate.profile.name}: score {candidate.score.overall:.0f}, {candidate.score.band}. {details}")
        return "\n".join(rows) or "No candidates match."

    def run(self, tool: str, arguments: dict) -> str:
        """Run one tool. Unknown tools or bad arguments give a message the model can recover from."""
        if tool not in TOOL_NAMES:
            return f"There is no tool called '{tool}'. Use one of: {', '.join(TOOL_NAMES)}."
        function = getattr(self, tool)
        try:
            return function(**arguments)
        except TypeError:
            return f"Wrong arguments for {tool}. Example: {tool}(query) or {tool}(name)."

    def verify(self, citation: Citation) -> bool:
        """Is the quote really in that candidate's CV?"""
        candidate = self.find(citation.candidate)
        if candidate is None:
            return False
        return guardrails.is_quote_verified(citation.quote, self.cv_texts.get(candidate.candidate_id, ""))


# ===========================================================================
# The loop
# ===========================================================================


def tool_results_text(calls: list[ToolCall]) -> str:
    if not calls:
        return "(no tools called yet)"
    return "\n\n".join(f"{call.label()} returned:\n{call.result}" for call in calls)


def ask_step(llm, question: str, calls: list[ToolCall], must_answer: bool) -> AssistantStep:
    template = prompts.ASSISTANT
    if must_answer:
        instruction = "You have used all your tool calls. Answer now with action \"answer\"."
    else:
        instruction = "Call a tool if you need more information, otherwise answer."
    prompt = prompts.fill(
        template.user,
        {
            "question": prompts.neutralise_tags(question),
            "tool_results": prompts.neutralise_tags(tool_results_text(calls)),
            "instruction": instruction,
            "schema": prompts.schema_text(AssistantStep),
        },
    )
    step, _meta = llm.generate_json("assistant", template.system, prompt, AssistantStep, template.temperature,
                                    template.version)
    return step


def answer_question(llm, question: str, tools: CVTools) -> AssistantAnswer:
    """Run the tool loop: at most MAX_TOOL_CALLS tool calls, then a final answer with verified citations."""
    calls: list[ToolCall] = []
    step = ask_step(llm, question, calls, must_answer=False)
    while step.action == "call_tool" and len(calls) < MAX_TOOL_CALLS:
        tool = step.tool or ""
        calls.append(ToolCall(tool, step.arguments, tools.run(tool, step.arguments)))
        step = ask_step(llm, question, calls, must_answer=len(calls) >= MAX_TOOL_CALLS)

    if step.action != "answer" or not (step.answer or "").strip():
        return AssistantAnswer(NOT_FOUND_ANSWER, [], calls)

    for citation in step.citations:
        citation.verified = tools.verify(citation)
    answer, _removed = guardrails.filter_protected_sentences(step.answer)  # the bias filter
    return AssistantAnswer(answer or NOT_FOUND_ANSWER, step.citations, calls)
