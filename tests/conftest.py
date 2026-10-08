"""Shared test helpers: paths to the sample data, and a ready-to-use CV reader.

pytest loads this file automatically before running any test.
"""

from pathlib import Path

import pytest

from core.documents import read_document_file

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = PROJECT_ROOT / "data" / "sample"
CV_DIR = SAMPLE_DIR / "cvs"
LIVE_DEMO_DIR = SAMPLE_DIR / "live_demo"

# All 7 fictional CVs: the 6 sample CVs plus Nadia (used live in the demo).
ALL_CV_PATHS: list[Path] = sorted(CV_DIR.iterdir()) + sorted(LIVE_DEMO_DIR.iterdir())


def cv_path(file_name: str) -> Path:
    """Find a sample CV by file name, in either folder."""
    for path in ALL_CV_PATHS:
        if path.name == file_name:
            return path
    raise FileNotFoundError(file_name)


@pytest.fixture
def read_cv():
    """Lets a test write read_cv("Ryan_Chen_CV.pdf") to get the cleaned text."""

    def reader(file_name: str) -> str:
        return read_document_file(cv_path(file_name))

    return reader
