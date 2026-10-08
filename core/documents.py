"""Reads PDF, DOCX, TXT and MD files and turns them into clean plain text.

Why this file exists:
- CVs and job descriptions arrive as different file types. The agents only
  ever see plain text, so this is the single place that converts them.
- Files that have almost no text (empty, or a scanned image with no text layer)
  are stopped here with a friendly message instead of a Python error.
"""

from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from pathlib import Path

import docx
from pypdf import PdfReader

SUPPORTED_EXTENSIONS: set[str] = {".pdf", ".docx", ".txt", ".md"}

# A file with fewer words than this is treated as "almost empty" (spec FR-C2).
MIN_WORDS: int = 50

# Characters that PDFs and Word use as bullet points. They all become "- ".
BULLET_CHARACTERS: set[str] = {"•", "●", "▪", "■", "◦", "‣", "∙", "·", "", "\x7f"}


class DocumentReadError(Exception):
    """Raised when a file can't be turned into usable text.

    The message is written for the recruiter, so the UI can show it as-is.
    """


def content_hash(data: bytes) -> str:
    """A fingerprint of the file's bytes. Two identical files get the same hash."""
    return hashlib.sha256(data).hexdigest()


def read_pdf(data: bytes) -> str:
    """Pull the text out of every page of a PDF."""
    try:
        reader = PdfReader(io.BytesIO(data))
        page_texts = []
        for page in reader.pages:
            page_texts.append(page.extract_text() or "")
    except Exception as error:  # pypdf raises many different error types
        raise DocumentReadError(
            "We couldn't open this PDF. It may be damaged or password-protected."
        ) from error
    return "\n".join(page_texts)


def read_docx(data: bytes) -> str:
    """Pull the text out of a Word file: every paragraph, then every table cell."""
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as error:
        raise DocumentReadError(
            "We couldn't open this Word file. Please save it again as .docx and retry."
        ) from error

    lines = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            cell_texts = [cell.text.strip() for cell in row.cells]
            lines.append(" | ".join(cell_texts))
    return "\n".join(lines)


def read_plain_text(data: bytes) -> str:
    """Decode a .txt or .md file. Tries UTF-8 first, then the usual Windows encoding."""
    try:
        return data.decode("utf-8-sig")  # utf-8-sig also removes a leading BOM
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def is_bullet_or_junk(character: str) -> bool:
    """True for bullet symbols and invisible control or private-use characters."""
    if character in BULLET_CHARACTERS:
        return True
    category = unicodedata.category(character)
    # Cc = control characters, Co = private-use symbols (often icon fonts in PDFs)
    return category in {"Cc", "Co"} and character not in {"\n", "\t"}


def clean_line(line: str) -> str:
    """Tidy one line: bullets become "- ", junk characters go, spaces are collapsed."""
    stripped = line.strip()
    starts_with_bullet = stripped != "" and is_bullet_or_junk(stripped[0])

    kept_characters = []
    for character in stripped:
        if is_bullet_or_junk(character):
            kept_characters.append(" ")
        else:
            kept_characters.append(character)
    text = "".join(kept_characters)
    text = re.sub(r"[ \t]+", " ", text).strip()

    if starts_with_bullet and text:
        return "- " + text
    return text


def clean_text(raw_text: str) -> str:
    """Clean a whole document, keeping one line per line and at most one blank line in a row."""
    # NFKC turns look-alike characters (e.g. the "ﬁ" ligature) into plain ones.
    normalised = unicodedata.normalize("NFKC", raw_text)
    normalised = normalised.replace("\r\n", "\n").replace("\r", "\n")

    cleaned_lines: list[str] = []
    for line in normalised.split("\n"):
        cleaned = clean_line(line)
        previous_was_blank = len(cleaned_lines) > 0 and cleaned_lines[-1] == ""
        if cleaned == "" and (previous_was_blank or len(cleaned_lines) == 0):
            continue  # skip leading blanks and repeated blank lines
        cleaned_lines.append(cleaned)
    return "\n".join(cleaned_lines).strip()


def count_words(text: str) -> int:
    return len(text.split())


def read_document(file_name: str, data: bytes) -> str:
    """Turn an uploaded file into clean text.

    Raises DocumentReadError with a friendly message if the file type isn't
    supported, the file can't be opened, or it has fewer than MIN_WORDS words.
    """
    extension = Path(file_name).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise DocumentReadError(
            f"'{file_name}' isn't a supported file type. Please upload a PDF, DOCX or TXT file."
        )

    if extension == ".pdf":
        raw_text = read_pdf(data)
    elif extension == ".docx":
        raw_text = read_docx(data)
    else:
        raw_text = read_plain_text(data)

    text = clean_text(raw_text)
    words = count_words(text)
    if words == 0:
        raise DocumentReadError("No text found. Is this a scanned image?")
    if words < MIN_WORDS:
        raise DocumentReadError(
            f"Only {words} words of text found. Is this a scanned image?"
        )
    return text


def read_document_file(path: Path) -> str:
    """Same as read_document, but for a file on disk (used by scripts and tests)."""
    return read_document(path.name, path.read_bytes())
