"""Memory: everything TalentLens saves in the database (spec section 11).

Why this file exists:
- Recruiter preferences, approved requirements, loaded CVs, screening runs,
  human decisions, reports, cached AI answers and the audit log must survive
  closing the app. This file is the only place that reads or writes them.
- It uses SQLAlchemy, so the same code works with SQLite on your laptop and
  with Postgres in the cloud. DATABASE_URL picks which one.

Rules enforced here (not in the UI, so they can't be skipped):
- A decision must be Pending, Shortlist, Hold or Reject.
- A rejection needs a written reason of at least 10 characters (Guidelines §8.2).
- Every change to preferences, jobs, decisions and reports adds an audit row.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    Connection,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.engine import Engine

from core.config import DEFAULT_SQLITE_PATH
from core.schemas import IntakeResult, JobRequirements, RunResult

ALLOWED_DECISIONS: list[str] = ["Pending", "Shortlist", "Hold", "Reject"]
MIN_REJECTION_REASON_LENGTH: int = 10
REJECTION_REASON_MESSAGE = "A rejection needs a written, job-related reason (Hiring Guidelines §8.2)"


class DecisionError(ValueError):
    """Raised when a decision breaks a rule. The message is safe to show the recruiter."""


def now_text() -> str:
    """The current time in UTC as text, e.g. "2026-10-08T14:30:00+00:00"."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ===========================================================================
# Table definitions
# ===========================================================================

metadata = MetaData()

preferences_table = Table(
    "preferences",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("text", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
)

jobs_table = Table(
    "jobs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("title", String(300), nullable=False),
    Column("jd_text", Text, nullable=False),
    Column("requirements_json", Text, nullable=False),
    Column("approved_by", String(200)),
    Column("approved_at", String(40)),
)

documents_table = Table(
    "documents",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kind", String(40), nullable=False),  # "cv" for now
    Column("file_name", String(300), nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("clean_text", Text, nullable=False),  # already redacted: no protected details
    Column("word_count", Integer, nullable=False),
    Column("redactions_json", Text, nullable=False),  # labels only
    Column("quarantined_json", Text, nullable=False),
    Column("active", Boolean, nullable=False, default=True),
    Column("created_at", String(40), nullable=False),
)

runs_table = Table(
    "runs",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("job_id", Integer),
    Column("created_at", String(40), nullable=False),
    Column("provider", String(40), nullable=False),
    Column("model", String(100), nullable=False),
    Column("payload_json", Text, nullable=False),  # the full RunResult
)

decisions_table = Table(
    "decisions",
    metadata,
    Column("run_id", String(64), primary_key=True),
    Column("candidate_id", String(64), primary_key=True),
    Column("candidate_name", String(200), nullable=False),
    Column("decision", String(20), nullable=False),
    Column("reason", Text),
    Column("reviewer", String(200), nullable=False),
    Column("ai_band", String(60)),
    Column("ai_score", Float),
    Column("decided_at", String(40), nullable=False),
)

reports_table = Table(
    "reports",
    metadata,
    Column("run_id", String(64), primary_key=True),
    Column("draft_md", Text),
    Column("final_md", Text),
    Column("approved_by", String(200)),
    Column("approved_at", String(40)),
)

audit_table = Table(
    "audit",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", String(40), nullable=False),
    Column("run_id", String(64)),
    Column("actor", String(200), nullable=False),
    Column("event", String(80), nullable=False),
    Column("detail", Text),
)

llm_cache_table = Table(
    "llm_cache",
    metadata,
    Column("key", String(64), primary_key=True),
    Column("task", String(80)),
    Column("provider", String(40)),
    Column("model", String(100)),
    Column("response_json", Text, nullable=False),
    Column("meta_json", Text),
    Column("created_at", String(40), nullable=False),
)


# ===========================================================================
# Connecting
# ===========================================================================


def default_database_url() -> str:
    """The SQLite file in data/talentlens.db (the folder is created if needed)."""
    DEFAULT_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return "sqlite:///" + DEFAULT_SQLITE_PATH.as_posix()


def to_sqlalchemy_url(database_url: str) -> str:
    """Pick the database driver from the URL.

    Neon gives a URL starting "postgresql://" (or "postgres://"). SQLAlchemy needs to be
    told to use the psycopg driver, so it becomes "postgresql+psycopg://".
    An empty URL means the local SQLite file.
    """
    url = database_url.strip()
    if not url:
        return default_database_url()
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def make_engine(database_url: str) -> Engine:
    """Connect to the database: SQLite locally, Postgres (Neon) in the cloud."""
    url = to_sqlalchemy_url(database_url)
    if url.startswith("postgresql"):
        # Neon's database sleeps after 5 minutes idle and drops old connections.
        # pool_pre_ping tests each connection before use; pool_recycle replaces old ones.
        return create_engine(url, pool_pre_ping=True, pool_recycle=240)
    return create_engine(url, pool_pre_ping=True)


class Memory:
    """The app's database. Create one with Memory(settings.database_url)."""

    def __init__(self, database_url: str = "") -> None:
        self.engine = make_engine(database_url)
        metadata.create_all(self.engine)  # creates any missing tables

    def close(self) -> None:
        """Release the database connection (used by tests)."""
        self.engine.dispose()

    # -----------------------------------------------------------------------
    # Audit log (FR-M4)
    # -----------------------------------------------------------------------

    def write_audit(
        self, connection: Connection, actor: str, event: str, detail: str = "", run_id: str | None = None
    ) -> None:
        """Add one audit row, inside the same transaction as the change it describes."""
        connection.execute(
            insert(audit_table).values(
                ts=now_text(), run_id=run_id, actor=actor, event=event, detail=detail
            )
        )

    def add_audit(self, actor: str, event: str, detail: str = "", run_id: str | None = None) -> None:
        """Add an audit row on its own, for actions that change nothing else."""
        with self.engine.begin() as connection:
            self.write_audit(connection, actor, event, detail, run_id)

    def latest_event_time(self, event: str) -> str | None:
        """When an audit event last happened (e.g. "new_screening"), or None if never."""
        query = (
            select(audit_table.c.ts)
            .where(audit_table.c.event == event)
            .order_by(audit_table.c.id.desc())
            .limit(1)
        )
        with self.engine.connect() as connection:
            row = connection.execute(query).first()
        return row[0] if row else None

    def list_audit(self, run_id: str | None = None, limit: int = 200) -> list[dict]:
        """The newest audit rows first. Give a run_id to see only that run."""
        query = select(audit_table).order_by(audit_table.c.id.desc()).limit(limit)
        if run_id is not None:
            query = query.where(audit_table.c.run_id == run_id)
        with self.engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return [dict(row) for row in rows]

    # -----------------------------------------------------------------------
    # Preferences (FR-M1)
    # -----------------------------------------------------------------------

    def add_preference(self, text: str, actor: str) -> int:
        """Save a recruiter preference, e.g. "TikTok experience matters a lot"."""
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("A preference can't be empty.")
        with self.engine.begin() as connection:
            result = connection.execute(
                insert(preferences_table).values(text=cleaned, created_at=now_text())
            )
            new_id = result.inserted_primary_key[0]
            self.write_audit(connection, actor, "preference_added", cleaned)
        return int(new_id)

    def list_preferences(self) -> list[dict]:
        query = select(preferences_table).order_by(preferences_table.c.id)
        with self.engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return [dict(row) for row in rows]

    def delete_preference(self, preference_id: int, actor: str) -> None:
        with self.engine.begin() as connection:
            row = connection.execute(
                select(preferences_table).where(preferences_table.c.id == preference_id)
            ).mappings().first()
            if row is None:
                return
            connection.execute(delete(preferences_table).where(preferences_table.c.id == preference_id))
            self.write_audit(connection, actor, "preference_deleted", row["text"])

    # -----------------------------------------------------------------------
    # Jobs: approved requirements (FR-J5, FR-J6)
    # -----------------------------------------------------------------------

    def save_approved_job(self, jd_text: str, requirements: JobRequirements, approved_by: str) -> int:
        """Save approved requirements under the job title.

        If a job with the same title exists it is updated, so the latest approved
        checklist is offered next time. Returns the job id.
        """
        values = {
            "title": requirements.title,
            "jd_text": jd_text,
            "requirements_json": requirements.model_dump_json(),
            "approved_by": approved_by,
            "approved_at": now_text(),
        }
        with self.engine.begin() as connection:
            existing = connection.execute(
                select(jobs_table.c.id).where(jobs_table.c.title == requirements.title)
            ).first()
            if existing is None:
                result = connection.execute(insert(jobs_table).values(**values))
                job_id = int(result.inserted_primary_key[0])
            else:
                job_id = int(existing[0])
                connection.execute(update(jobs_table).where(jobs_table.c.id == job_id).values(**values))
            self.write_audit(connection, approved_by, "requirements_approved", requirements.title)
        return job_id

    def row_to_job(self, row) -> dict:
        job = dict(row)
        job["requirements"] = JobRequirements.model_validate_json(job.pop("requirements_json"))
        return job

    def get_job(self, job_id: int) -> dict | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(jobs_table).where(jobs_table.c.id == job_id)).mappings().first()
        return self.row_to_job(row) if row else None

    def get_job_by_title(self, title: str) -> dict | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(jobs_table).where(jobs_table.c.title == title)).mappings().first()
        return self.row_to_job(row) if row else None

    def get_latest_job(self) -> dict | None:
        """The most recently approved job (reloaded when the app starts)."""
        query = select(jobs_table).order_by(jobs_table.c.approved_at.desc()).limit(1)
        with self.engine.connect() as connection:
            row = connection.execute(query).mappings().first()
        return self.row_to_job(row) if row else None

    def list_jobs(self) -> list[dict]:
        query = select(jobs_table.c.id, jobs_table.c.title, jobs_table.c.approved_by, jobs_table.c.approved_at)
        with self.engine.connect() as connection:
            rows = connection.execute(query.order_by(jobs_table.c.id)).mappings().all()
        return [dict(row) for row in rows]

    # -----------------------------------------------------------------------
    # Documents: loaded CVs after intake (FR-C1, FR-C5)
    # -----------------------------------------------------------------------

    def add_document(self, intake: IntakeResult, kind: str = "cv") -> tuple[int, bool]:
        """Save a cleaned CV in the working area.

        A file with the same name and content as one already loaded is ignored (FR-C5).
        Returns (document id, True if it was newly added).
        """
        with self.engine.begin() as connection:
            existing = connection.execute(
                select(documents_table.c.id).where(
                    documents_table.c.kind == kind,
                    documents_table.c.file_name == intake.file_name,
                    documents_table.c.content_hash == intake.content_hash,
                    documents_table.c.active == True,  # noqa: E712 (SQLAlchemy needs ==)
                )
            ).first()
            if existing is not None:
                return int(existing[0]), False

            result = connection.execute(
                insert(documents_table).values(
                    kind=kind,
                    file_name=intake.file_name,
                    content_hash=intake.content_hash,
                    clean_text=intake.clean_text,
                    word_count=intake.word_count,
                    redactions_json=json.dumps(intake.redactions),
                    quarantined_json=json.dumps(intake.quarantined_text),
                    active=True,
                    created_at=now_text(),
                )
            )
            return int(result.inserted_primary_key[0]), True

    def row_to_document(self, row) -> dict:
        document = dict(row)
        document["redactions"] = json.loads(document.pop("redactions_json"))
        document["quarantined_text"] = json.loads(document.pop("quarantined_json"))
        return document

    def list_documents(self, kind: str = "cv") -> list[dict]:
        """The documents currently in the working area, oldest first."""
        query = (
            select(documents_table)
            .where(documents_table.c.kind == kind, documents_table.c.active == True)  # noqa: E712
            .order_by(documents_table.c.id)
        )
        with self.engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return [self.row_to_document(row) for row in rows]

    def get_document(self, document_id: int) -> dict | None:
        with self.engine.connect() as connection:
            row = connection.execute(
                select(documents_table).where(documents_table.c.id == document_id)
            ).mappings().first()
        return self.row_to_document(row) if row else None

    def remove_document(self, document_id: int) -> None:
        """Remove a CV before screening (FR-C5)."""
        with self.engine.begin() as connection:
            connection.execute(delete(documents_table).where(documents_table.c.id == document_id))

    def clear_working_documents(self, kind: str | None = None) -> None:
        """Take documents out of the working area, keeping them as history.

        With no kind, every document is cleared (for "New screening").
        """
        statement = update(documents_table).values(active=False)
        if kind is not None:
            statement = statement.where(documents_table.c.kind == kind)
        with self.engine.begin() as connection:
            connection.execute(statement)

    def start_new_screening(self, actor: str) -> None:
        """FR-M5: clear the working area but keep preferences, jobs and past runs.

        The audit row's time marks the start of the new working area, so on restart
        the app only reloads jobs and runs created after it.
        """
        self.clear_working_documents()
        self.add_audit(actor, "new_screening", "Working area cleared")

    # -----------------------------------------------------------------------
    # Runs (FR-S4, FR-M2, FR-M3)
    # -----------------------------------------------------------------------

    def save_run(self, run: RunResult, job_id: int | None = None) -> None:
        """Save (or overwrite) a run. Called after every candidate, so work is never lost."""
        values = {
            "job_id": job_id,
            "created_at": run.created_at,
            "provider": run.provider,
            "model": run.model,
            "payload_json": run.model_dump_json(),
        }
        with self.engine.begin() as connection:
            existing = connection.execute(select(runs_table.c.id).where(runs_table.c.id == run.run_id)).first()
            if existing is None:
                connection.execute(insert(runs_table).values(id=run.run_id, **values))
            else:
                connection.execute(update(runs_table).where(runs_table.c.id == run.run_id).values(**values))

    def load_run(self, run_id: str) -> RunResult | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(runs_table.c.payload_json).where(runs_table.c.id == run_id)).first()
        return RunResult.model_validate_json(row[0]) if row else None

    def load_latest_run(self) -> RunResult | None:
        """The most recent run, reloaded when the app starts (FR-M2)."""
        query = select(runs_table.c.payload_json).order_by(runs_table.c.created_at.desc()).limit(1)
        with self.engine.connect() as connection:
            row = connection.execute(query).first()
        return RunResult.model_validate_json(row[0]) if row else None

    def list_runs(self) -> list[dict]:
        """A short summary of every run, newest first (no payloads)."""
        query = select(
            runs_table.c.id, runs_table.c.job_id, runs_table.c.created_at, runs_table.c.provider, runs_table.c.model
        ).order_by(runs_table.c.created_at.desc())
        with self.engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return [dict(row) for row in rows]

    # -----------------------------------------------------------------------
    # Decisions: the human checkpoint (FR-D1 to FR-D5)
    # -----------------------------------------------------------------------

    def check_decision(self, decision: str, reason: str | None) -> None:
        """Raise DecisionError if the decision breaks a rule."""
        if decision not in ALLOWED_DECISIONS:
            raise DecisionError(f"'{decision}' isn't a valid decision. Choose Pending, Shortlist, Hold or Reject.")
        if decision == "Reject":
            written_reason = (reason or "").strip()
            if len(written_reason) < MIN_REJECTION_REASON_LENGTH:
                raise DecisionError(REJECTION_REASON_MESSAGE)

    def save_decision(
        self,
        run_id: str,
        candidate_id: str,
        candidate_name: str,
        decision: str,
        reason: str | None,
        reviewer: str,
        ai_band: str | None,
        ai_score: float | None,
    ) -> None:
        """Record (or change) a recruiter's decision, with the AI band and score at that moment."""
        self.check_decision(decision, reason)
        values = {
            "candidate_name": candidate_name,
            "decision": decision,
            "reason": (reason or "").strip(),
            "reviewer": reviewer,
            "ai_band": ai_band,
            "ai_score": ai_score,
            "decided_at": now_text(),
        }
        match_row = (decisions_table.c.run_id == run_id) & (decisions_table.c.candidate_id == candidate_id)
        with self.engine.begin() as connection:
            existing = connection.execute(select(decisions_table.c.decision).where(match_row)).first()
            if existing is None:
                connection.execute(
                    insert(decisions_table).values(run_id=run_id, candidate_id=candidate_id, **values)
                )
            else:
                connection.execute(update(decisions_table).where(match_row).values(**values))

            detail = f"{candidate_name}: {decision}"
            if values["reason"]:
                detail += f" (reason: {values['reason']})"
            self.write_audit(connection, reviewer, "decision_saved", detail, run_id)

    def get_decisions(self, run_id: str) -> dict[str, dict]:
        """All decisions for a run, keyed by candidate id."""
        query = select(decisions_table).where(decisions_table.c.run_id == run_id)
        with self.engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return {row["candidate_id"]: dict(row) for row in rows}

    def reset_decisions(self, run_id: str, actor: str) -> None:
        """Delete every decision for a run (FR-D5, for demo rehearsals). Logged in the audit."""
        with self.engine.begin() as connection:
            connection.execute(delete(decisions_table).where(decisions_table.c.run_id == run_id))
            self.write_audit(connection, actor, "decisions_reset", "All decisions cleared", run_id)

    # -----------------------------------------------------------------------
    # Reports (FR-P2 to FR-P4)
    # -----------------------------------------------------------------------

    def save_report_draft(self, run_id: str, draft_md: str, actor: str) -> None:
        """Save a new report draft. Any earlier approval is cleared, since the text changed."""
        values = {"draft_md": draft_md, "final_md": None, "approved_by": None, "approved_at": None}
        with self.engine.begin() as connection:
            existing = connection.execute(select(reports_table.c.run_id).where(reports_table.c.run_id == run_id)).first()
            if existing is None:
                connection.execute(insert(reports_table).values(run_id=run_id, **values))
            else:
                connection.execute(update(reports_table).where(reports_table.c.run_id == run_id).values(**values))
            self.write_audit(connection, actor, "report_drafted", "", run_id)

    def approve_report(self, run_id: str, final_md: str, approved_by: str) -> None:
        """Record the recruiter's approval of the final report text."""
        values = {"final_md": final_md, "approved_by": approved_by, "approved_at": now_text()}
        with self.engine.begin() as connection:
            existing = connection.execute(select(reports_table.c.run_id).where(reports_table.c.run_id == run_id)).first()
            if existing is None:
                connection.execute(insert(reports_table).values(run_id=run_id, draft_md=final_md, **values))
            else:
                connection.execute(update(reports_table).where(reports_table.c.run_id == run_id).values(**values))
            self.write_audit(connection, approved_by, "report_approved", "", run_id)

    def get_report(self, run_id: str) -> dict | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(reports_table).where(reports_table.c.run_id == run_id)).mappings().first()
        return dict(row) if row else None

    # -----------------------------------------------------------------------
    # LLM cache (spec section 10): saved AI answers, so repeat runs are instant
    # -----------------------------------------------------------------------

    def cache_get(self, key: str) -> dict | None:
        """Return {"response_json": ..., "meta_json": ...} for a cached answer, or None."""
        query = select(llm_cache_table.c.response_json, llm_cache_table.c.meta_json).where(
            llm_cache_table.c.key == key
        )
        with self.engine.connect() as connection:
            row = connection.execute(query).mappings().first()
        return dict(row) if row else None

    def cache_put(self, key: str, task: str, provider: str, model: str, response_json: str, meta_json: str) -> None:
        """Save (or replace) a cached AI answer."""
        values = {
            "task": task,
            "provider": provider,
            "model": model,
            "response_json": response_json,
            "meta_json": meta_json,
            "created_at": now_text(),
        }
        with self.engine.begin() as connection:
            existing = connection.execute(select(llm_cache_table.c.key).where(llm_cache_table.c.key == key)).first()
            if existing is None:
                connection.execute(insert(llm_cache_table).values(key=key, **values))
            else:
                connection.execute(update(llm_cache_table).where(llm_cache_table.c.key == key).values(**values))
