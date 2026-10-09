"""FakeLLM: a pretend AI model for tests. It never calls a real API.

Why this file exists:
- Tests must run offline, fast, and give the same answer every time.
- The fake answers each task (job_analyst, cv_analyst, comparison...) with
  scripted JSON, worked out from the prompt it receives.
- It records every prompt, so tests can check what a real model WOULD have seen,
  e.g. that Ryan's hidden text never reaches it.

Tests can replace any task's handler to simulate bad quotes, errors and so on.
"""

from __future__ import annotations

import json
import re
from typing import Callable

from core.llm import CallMeta

Handler = Callable[[str], dict]


def text_between(prompt: str, tag: str) -> str:
    """The text inside <tag>...</tag> in a prompt ("" if absent)."""
    match = re.search(rf"<{tag}>\n?(.*?)\n?</{tag}>", prompt, flags=re.DOTALL)
    return match.group(1) if match else ""


def requirement_ids(prompt: str) -> list[str]:
    """The requirement ids listed in the <job_requirements> section."""
    rows = json.loads(text_between(prompt, "job_requirements") or "[]")
    return [row["id"] for row in rows]


def first_lines(cv_text: str) -> list[str]:
    return [line.strip() for line in cv_text.splitlines() if line.strip()]


def labelled_value(cv_text: str, label: str) -> str | None:
    """Read 'Label: value' from the CV, e.g. 'Salary expectation: MUR 55,000 per month'."""
    match = re.search(rf"^{label}:\s*(.+)$", cv_text, flags=re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else None


# ---------------------------------------------------------------------------
# Default answers for each task
# ---------------------------------------------------------------------------

SAMPLE_REQUIREMENTS = {
    "title": "Marketing Executive",
    "company": "Corallia Living Ltd",
    "location": "Port Louis, Mauritius",
    "salary_min": 45000,
    "salary_max": 60000,
    "currency": "MUR",
    "must_have": [
        {"id": "M1", "label": "Digital marketing experience", "description": "At least 3 years",
         "keywords": ["digital marketing"], "min_years": 3, "weight": 2},
        {"id": "M2", "label": "Social media", "description": "Manage social channels",
         "keywords": ["Instagram", "TikTok"], "min_years": None, "weight": 2},
    ],
    "nice_to_have": [
        {"id": "N1", "label": "SEO", "description": "SEO knowledge", "keywords": ["SEO"], "min_years": None, "weight": 2},
    ],
    "preference_notes": [],
}


def default_job_analyst(prompt: str) -> dict:
    return json.loads(json.dumps(SAMPLE_REQUIREMENTS))  # a fresh copy each time


def default_cv_analyst(prompt: str) -> dict:
    """Build a profile from the CV text: name, contact line and 'Label: value' lines."""
    cv_text = text_between(prompt, "cv")
    lines = first_lines(cv_text)
    contact = lines[2] if len(lines) > 2 else ""
    contact_parts = [part.strip() for part in contact.split("|")]
    return {
        "name": lines[0] if lines else "Unknown",
        "headline": lines[1] if len(lines) > 1 else None,
        "email": contact_parts[0] if contact_parts and "@" in contact_parts[0] else None,
        "phone": contact_parts[1] if len(contact_parts) > 1 else None,
        "location": contact_parts[2] if len(contact_parts) > 2 else None,
        "roles": [],
        "salary_expectation": labelled_value(cv_text, "Salary expectation"),
        "notice_period": labelled_value(cv_text, "Notice period") or labelled_value(cv_text, "Availability"),
        "right_to_work": labelled_value(cv_text, "Right to work"),
        "references": labelled_value(cv_text, "References"),
    }


def default_comparison(prompt: str) -> dict:
    """Every requirement 'met', quoting the CV's headline line (which really is in the CV)."""
    cv_text = text_between(prompt, "cv")
    quote = first_lines(cv_text)[1]
    results = []
    for requirement_id in requirement_ids(prompt):
        results.append(
            {"requirement_id": requirement_id, "status": "met", "evidence": quote,
             "reasoning": "Direct evidence (§3.2).", "guideline_refs": ["§3.2"]}
        )
    return {"results": results, "strengths": ["Strong social media work"], "concerns": []}


def default_report_candidate(prompt: str) -> dict:
    return {
        "summary": "The candidate has relevant marketing experience. Their CV shows social media work.",
        "relevant_experience": "Several years in digital marketing",
        "key_skills": ["Social media", "Copywriting"],
        "interview_questions": [
            {"question": "Tell me about a campaign you planned from start to finish.", "purpose": "M2"},
            {"question": "Describe how you measure campaign results.", "purpose": "M1"},
            {"question": "How do you approach SEO for a retail website?", "purpose": "N1"},
        ],
    }


def default_report_shortlist(prompt: str) -> dict:
    return {"overview": "Two strong candidates were shortlisted.", "points_to_discuss": ["Collect notice periods."]}


def default_email(prompt: str) -> dict:
    """Asks for exactly the items listed in <missing_information>."""
    items = [line[2:] for line in text_between(prompt, "missing_information").splitlines() if line.startswith("- ")]
    body = "Dear candidate,\n\nThank you for applying. Could you please send:\n" + "\n".join(f"- {item}" for item in items)
    return {"subject": "Your application: a few details", "body": body + "\n\nKind regards,\nRecruiter"}


def default_assistant(prompt: str) -> dict:
    """First searches the CVs for the question, then answers quoting the first search hit."""
    results = text_between(prompt, "tool_results")
    if "returned:" not in results:
        return {"action": "call_tool", "tool": "search_cvs", "arguments": {"query": text_between(prompt, "question")}}
    hit = re.search(r'- (.+?): "(.+)"', results)
    if hit is None:
        return {"action": "answer", "answer": "I couldn't find that in the CVs.", "citations": []}
    return {"action": "answer", "answer": f"{hit.group(1)} mentions it.",
            "citations": [{"candidate": hit.group(1), "quote": hit.group(2)}]}


def default_handlers() -> dict[str, Handler]:
    return {
        "job_analyst": default_job_analyst,
        "cv_analyst": default_cv_analyst,
        "comparison": default_comparison,
        "comparison_revision": default_comparison,
        "report_candidate": default_report_candidate,
        "report_shortlist": default_report_shortlist,
        "email": default_email,
        "assistant": default_assistant,
    }


# ---------------------------------------------------------------------------
# The fake model
# ---------------------------------------------------------------------------


class FakeLLM:
    """Has the same generate_json(...) method as the real LLMClient."""

    provider = "fake"
    model = "fake-model"

    def __init__(self, handlers: dict[str, Handler] | None = None) -> None:
        self.handlers = default_handlers()
        if handlers:
            self.handlers.update(handlers)
        self.calls: list[dict] = []  # every request: task, system, prompt

    def generate_json(self, task, system, prompt, schema, temperature, prompt_version=None):
        self.calls.append({"task": task, "system": system, "prompt": prompt})
        data = self.handlers[task](prompt)  # a handler may raise to simulate a failure
        result = schema.model_validate(data)
        meta = CallMeta(
            task=task,
            provider=self.provider,
            model=self.model,
            prompt_version=prompt_version,
            duration_ms=1,
            tokens_in=len(system + prompt) // 4,
            tokens_out=len(json.dumps(data)) // 4,
            calls=1,
        )
        return result, meta

    def tasks_called(self) -> list[str]:
        return [call["task"] for call in self.calls]

    def everything_sent(self) -> str:
        """All system and user text sent so far, joined: for 'never in any prompt' checks."""
        return "\n".join(call["system"] + "\n" + call["prompt"] for call in self.calls)
