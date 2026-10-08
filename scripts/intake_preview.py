"""Shows what the intake guardrails do to each sample CV, before any AI is involved.

Run it with:  python scripts/intake_preview.py

For every sample CV it prints the word count, which protected details were
redacted (labels only), and any lines quarantined as hidden instructions.
Why: Phase 1 check. Jean-Marc should show 3 redactions, Ryan 1 quarantined
line, and the other 5 CVs nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Let this script import from `core/` even though it lives in `scripts/`.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.documents import DocumentReadError, content_hash, read_document  # noqa: E402
from core.guardrails import run_intake  # noqa: E402

SAMPLE_FOLDERS = [
    PROJECT_ROOT / "data" / "sample" / "cvs",
    PROJECT_ROOT / "data" / "sample" / "live_demo",
]


def find_sample_cvs() -> list[Path]:
    paths: list[Path] = []
    for folder in SAMPLE_FOLDERS:
        paths.extend(sorted(folder.iterdir()))
    return paths


def preview_cv(path: Path) -> None:
    """Print the intake result for one CV."""
    data = path.read_bytes()
    try:
        text = read_document(path.name, data)
    except DocumentReadError as error:
        print(f"{path.name}\n  Could not read: {error}\n")
        return

    intake = run_intake(path.name, text, content_hash(data))
    print(path.name)
    print(f"  Words: {intake.word_count}")
    if intake.redactions:
        print(f"  Redacted ({len(intake.redactions)}): " + ", ".join(intake.redactions))
    else:
        print("  Redacted: nothing")
    if intake.quarantined_text:
        print(f"  Quarantined ({len(intake.quarantined_text)}):")
        for line in intake.quarantined_text:
            print(f'    "{line}"')
    else:
        print("  Quarantined: nothing")
    print()


def main() -> None:
    print("TalentLens intake preview (what the AI will NOT see)\n")
    for path in find_sample_cvs():
        preview_cv(path)


if __name__ == "__main__":
    main()
