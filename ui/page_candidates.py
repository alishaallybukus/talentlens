"""Page 2, Candidates: upload CVs (or use the sample CVs), see the intake badges, preview, remove.

Why this file exists:
- Every CV goes through intake as soon as it's added: it's read into text,
  protected details are redacted, and hidden instructions are quarantined
  BEFORE any AI sees it (FR-C1 to FR-C5).
- The recruiter can see exactly what the AI will see.
"""

from __future__ import annotations

import streamlit as st

from core.config import Settings
from core.documents import DocumentReadError, content_hash, read_document
from core.guardrails import run_intake
from core.memory import Memory
from ui import components, state


def add_cv(memory: Memory, file_name: str, data: bytes) -> tuple[bool, str]:
    """Read, clean and save one CV. Returns (added, message for the recruiter)."""
    try:
        text = read_document(file_name, data)
    except DocumentReadError as error:
        return False, f"{file_name}: {error}"
    intake = run_intake(file_name, text, content_hash(data))
    _, is_new = memory.add_document(intake, kind="cv")
    if not is_new:
        return False, f"{file_name} is already loaded."
    return True, f"{file_name} added."


def add_uploads(memory: Memory, uploads) -> None:
    for upload in uploads:
        added, message = add_cv(memory, upload.name, upload.getvalue())
        if added:
            st.toast(message)
        else:
            st.warning(message)


def add_sample_cvs(memory: Memory) -> None:
    added_count = 0
    for path in sorted(state.SAMPLE_CV_DIR.iterdir()):
        added, message = add_cv(memory, path.name, path.read_bytes())
        if added:
            added_count += 1
    st.toast(f"{added_count} sample CV(s) added." if added_count else "The sample CVs are already loaded.")


def show_upload_area(memory: Memory) -> None:
    columns = st.columns([3, 1])
    with columns[0]:
        # A new key after each upload clears the box, so files aren't added twice.
        uploads = st.file_uploader(
            "Upload CVs (.pdf, .docx, .txt)",
            type=["pdf", "docx", "txt"],
            accept_multiple_files=True,
            key=f"cv_uploader_{st.session_state['uploader_key']}",
        )
        if uploads and st.button("Add these CVs", type="primary"):
            add_uploads(memory, uploads)
            st.session_state["uploader_key"] += 1
            st.rerun()
    with columns[1]:
        st.write("")
        st.write("")
        if st.button("Use sample CVs", help="Loads the 6 fictional sample CVs"):
            add_sample_cvs(memory)
            st.rerun()


def show_document(memory: Memory, document: dict, screened_ids: set[str]) -> None:
    """One CV row: name, badges, preview and remove button."""
    redactions = document["redactions"]
    quarantined = document["quarantined_text"]
    badges = components.intake_badges(document["word_count"], len(redactions), len(quarantined))
    if document["content_hash"][:12] in screened_ids:
        badges += " " + components.chip("✔ Screened", "met")

    with st.container(border=True):
        columns = st.columns([5, 1])
        with columns[0]:
            components.show(f'<div class="tl-card-title">{components.safe(document["file_name"])}</div>{badges}')
        with columns[1]:
            if st.button("Remove", key=f"remove_{document['id']}"):
                memory.remove_document(document["id"])
                st.toast(f"{document['file_name']} removed.")
                st.rerun()

        if redactions:
            st.caption("Redacted before analysis (labels only): " + ", ".join(redactions))
        if quarantined:
            st.warning(
                "**Suspicious text removed.** This line looked like an instruction to an AI tool. "
                "It was removed before analysis (Guidelines §8.3):\n\n> " + "\n> ".join(quarantined)
            )
        with st.expander("What the AI will see"):
            st.text(document["clean_text"])


def render(settings: Settings, memory: Memory) -> None:
    st.header("2. Candidates")
    st.markdown(
        "Add the CVs to screen. Each CV is cleaned **before** any AI sees it: protected details "
        "(age, marital status...) are redacted and hidden instructions are removed."
    )
    show_upload_area(memory)

    documents = memory.list_documents(kind="cv")
    if not documents:
        st.info("Upload CVs or click **Use sample CVs** to begin.")
        return

    run = state.current_run()
    screened_ids = {c.candidate_id for c in run.candidates if c.error is None} if run else set()
    st.markdown(f"#### {len(documents)} CV(s) loaded")
    for document in documents:
        show_document(memory, document, screened_ids)
