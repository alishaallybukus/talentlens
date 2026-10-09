"""Write docs/prompts.md: every agent prompt, with its version, straight from core/prompts.py.

Run it with:  python scripts/export_prompts.py

Why this file exists: the submission asks for the key prompts. Generating the
document from the code means it can never drift out of date.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so "core" can be imported

from core import prompts  # noqa: E402
from core.config import PROJECT_ROOT  # noqa: E402

OUTPUT_FILE = PROJECT_ROOT / "docs" / "prompts.md"

PURPOSES: dict[str, str] = {
    "job_analyst": "Turns the job description into an editable requirements checklist (spec 5.2).",
    "cv_analyst": "Extracts facts from one cleaned CV, without judging them (spec 5.3).",
    "comparison": "Decides met, partial or missing for every requirement, with a word-for-word CV quote (spec 5.4).",
    "report_candidate": "Writes the brief-format summary and 3 to 5 behavioural interview questions (spec 5.6).",
    "report_shortlist": "Writes the overview of the recruiter's shortlist, using only the data given (spec 5.6).",
    "email": "Drafts an email asking a candidate for exactly the missing details; never sent automatically (FR-X6).",
    "assistant": "Ask the CVs: a tool-calling assistant that answers questions with verified quotes (spec 5.8).",
}


def fenced(text: str) -> str:
    return "````text\n" + text.strip() + "\n````"


def build_document() -> str:
    lines = [
        "# TalentLens: key prompts",
        "",
        "Generated from `core/prompts.py` by `scripts/export_prompts.py`. Do not edit by hand.",
        "",
        "Markers like `<<cv>>` are filled with real data at run time. CV text always sits inside",
        "`<cv>...</cv>` and is treated as data, never instructions. Every answer must be JSON that",
        "matches a Pydantic model in `core/schemas.py`; invalid JSON is sent back for repair (up to 2 times).",
        "How each prompt changed after testing is in `docs/refinement_log.md`.",
        "",
        "| Prompt | Version | Temperature | Purpose |",
        "|---|---|---|---|",
    ]
    for template in prompts.ALL_PROMPTS:
        lines.append(f"| {template.name} | `{template.version}` | {template.temperature} | {PURPOSES.get(template.name, '')} |")
    lines += ["", "## Rules shared by every agent", "", fenced(prompts.SHARED_RULES), ""]
    for template in prompts.ALL_PROMPTS:
        lines += [
            f"## {template.name} (`{template.version}`)",
            "",
            PURPOSES.get(template.name, ""),
            "",
            "**System message**",
            "",
            fenced(template.system),
            "",
            "**User message template**",
            "",
            fenced(template.user),
            "",
        ]
        if template.name == "comparison":
            lines += ["**Revision instructions** (added when quotes weren't found in the CV)", "",
                      fenced(prompts.COMPARISON_REVISION_INSTRUCTIONS), ""]
    return "\n".join(lines)


def main() -> None:
    OUTPUT_FILE.write_text(build_document(), encoding="utf-8")
    print(f"Wrote {OUTPUT_FILE.relative_to(PROJECT_ROOT)} ({len(prompts.ALL_PROMPTS)} prompts)")


if __name__ == "__main__":
    main()
