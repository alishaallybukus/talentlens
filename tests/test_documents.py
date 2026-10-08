"""Tests for core/documents.py: every sample CV is read, and bad files get a friendly error."""

import io

import pytest
from pypdf import PdfWriter

from core.documents import MIN_WORDS, DocumentReadError, read_document
from tests.conftest import ALL_CV_PATHS


def test_there_are_seven_sample_cvs() -> None:
    assert len(ALL_CV_PATHS) == 7


@pytest.mark.parametrize("path", ALL_CV_PATHS, ids=lambda path: path.name)
def test_every_sample_cv_is_read(path) -> None:
    text = read_document(path.name, path.read_bytes())
    assert len(text.split()) >= MIN_WORDS
    # The candidate's name is on the first line of every sample CV.
    first_name = path.name.split("_")[0]
    assert first_name in text.splitlines()[0]


def test_docx_cv_is_read(read_cv) -> None:
    text = read_cv("Kevin_Ramdin_CV.docx")
    assert "Kevin Ramdin" in text
    assert "Google Analytics 4" in text


def test_pdf_bullets_become_dashes(read_cv) -> None:
    text = read_cv("Sarah_Moutou_CV.pdf")
    assert "- Manage Instagram, Facebook and TikTok channels" in text
    assert "\x7f" not in text


def make_blank_pdf() -> bytes:
    """A PDF with one empty page, like a scanned image with no text layer."""
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_image_only_pdf_gives_friendly_error() -> None:
    with pytest.raises(DocumentReadError, match="No text found. Is this a scanned image?"):
        read_document("scan.pdf", make_blank_pdf())


def test_empty_text_file_gives_friendly_error() -> None:
    with pytest.raises(DocumentReadError, match="scanned image"):
        read_document("empty.txt", b"")


def test_almost_empty_file_gives_friendly_error() -> None:
    with pytest.raises(DocumentReadError, match="Only 3 words"):
        read_document("short.txt", b"Jane Doe CV")


def test_damaged_pdf_gives_friendly_error() -> None:
    with pytest.raises(DocumentReadError, match="couldn't open this PDF"):
        read_document("broken.pdf", b"this is not really a pdf")


def test_unsupported_file_type_gives_friendly_error() -> None:
    with pytest.raises(DocumentReadError, match="isn't a supported file type"):
        read_document("photo.jpg", b"\xff\xd8\xff")


def test_text_file_is_read() -> None:
    words = " ".join(["marketing"] * 60)
    text = read_document("cv.txt", words.encode("utf-8"))
    assert text.startswith("marketing")
