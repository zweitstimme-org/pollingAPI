"""SQLAlchemy models for polling data."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from pollingapi.database import Base


class RawPoll(Base):
    """Raw poll data from scrapers."""

    __tablename__ = "polls_raw"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    public_id: Mapped[str | None] = mapped_column(String(16), unique=True, index=True)
    publish_date: Mapped[str | None] = mapped_column(String(50))
    survey_date_start: Mapped[str | None] = mapped_column(String(50))
    survey_date_end: Mapped[str | None] = mapped_column(String(50))
    respondents: Mapped[str | None] = mapped_column(String(100))
    zeitraum: Mapped[str | None] = mapped_column("Zeitraum", String(100))
    parties: Mapped[str | None] = mapped_column(Text)
    institute_id: Mapped[str | None] = mapped_column(String(100))
    provider: Mapped[str | None] = mapped_column(String(100))
    tasker: Mapped[str | None] = mapped_column(String(100))
    source: Mapped[str | None] = mapped_column(String(100))
    scope: Mapped[str | None] = mapped_column(String(100))
    election_id: Mapped[str | None] = mapped_column(String(100))
    method_id: Mapped[str | None] = mapped_column(String(100))
    worker: Mapped[str | None] = mapped_column(String(100))
    survey_type: Mapped[str | None] = mapped_column(String(100))
    duplicate_of_poll_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    date_downloaded: Mapped[str | None] = mapped_column(String(50))

    # Run traceability — links this row to the pipeline_runs record that ingested it.
    # No FK constraint yet; constraint migration is deferred.
    pipeline_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    # Relationship to cleaned poll
    cleaned_poll: Mapped[Poll | None] = relationship("Poll", back_populates="raw_poll")


class Institute(Base):
    """Polling institute."""

    __tablename__ = "institutes"

    key: Mapped[str] = mapped_column(String(50), primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)

    # Relationships
    polls: Mapped[list[Poll]] = relationship("Poll", back_populates="institute")


class Party(Base):
    """Political party."""

    __tablename__ = "parties"

    key: Mapped[str] = mapped_column(String(50), primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    short_name: Mapped[str | None] = mapped_column(String(20))
    color: Mapped[str | None] = mapped_column(String(7))  # Hex color
    external_ids: Mapped[dict[str, str] | None] = mapped_column(JSON)

    # Relationships
    poll_results: Mapped[list[PollResult]] = relationship("PollResult", back_populates="party")


class Provider(Base):
    """Data provider."""

    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)

    # Relationships
    polls: Mapped[list[Poll]] = relationship("Poll", back_populates="provider")


class Tasker(Base):
    """Tasker/commissioner."""

    __tablename__ = "taskers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)


class Election(Base):
    """Election type."""

    __tablename__ = "elections"

    key: Mapped[str] = mapped_column(String(50), primary_key=True, index=True)
    election_type: Mapped[str] = mapped_column(String(50), index=True)
    year: Mapped[int | None] = mapped_column(Integer)
    scope: Mapped[str | None] = mapped_column(String(50))
    date: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_is_estimated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Relationships
    polls: Mapped[list[Poll]] = relationship("Poll", back_populates="election")

    __table_args__ = (UniqueConstraint("election_type", "year", "scope", name="uix_election"),)


class Method(Base):
    """Survey method."""

    __tablename__ = "methods"

    key: Mapped[str] = mapped_column(String(50), primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)

    # Relationships
    polls: Mapped[list[Poll]] = relationship("Poll", back_populates="method")


class Poll(Base):
    """Clean, normalized poll data."""

    __tablename__ = "polls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    public_id: Mapped[str | None] = mapped_column(String(16), unique=True, index=True)
    raw_id: Mapped[int | None] = mapped_column(
        ForeignKey("polls_raw.id"), unique=True, nullable=True
    )
    publish_date: Mapped[date | None] = mapped_column(Date)
    survey_date_start: Mapped[date | None] = mapped_column(Date)
    survey_date_end: Mapped[date | None] = mapped_column(Date)
    respondents: Mapped[int | None] = mapped_column(Integer)
    institute_key: Mapped[str | None] = mapped_column(ForeignKey("institutes.key"))
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("providers.id"))
    election_key: Mapped[str | None] = mapped_column(ForeignKey("elections.key"))
    method_key: Mapped[str | None] = mapped_column(ForeignKey("methods.key"))
    source: Mapped[str | None] = mapped_column(String(100))
    scope: Mapped[str | None] = mapped_column(String(100))
    fingerprint: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    date_downloaded: Mapped[datetime | None] = mapped_column(DateTime)
    matching_poll_id: Mapped[int | None] = mapped_column(
        ForeignKey("polls.id"), nullable=True, index=True
    )
    matching_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    public_exclusion_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Relationships
    raw_poll: Mapped[RawPoll | None] = relationship("RawPoll", back_populates="cleaned_poll")
    institute: Mapped[Institute | None] = relationship("Institute", back_populates="polls")
    provider: Mapped[Provider | None] = relationship("Provider", back_populates="polls")
    election: Mapped[Election | None] = relationship("Election", back_populates="polls")
    method: Mapped[Method | None] = relationship("Method", back_populates="polls")
    results: Mapped[list[PollResult]] = relationship(
        "PollResult", back_populates="poll", cascade="all, delete-orphan"
    )
    validation: Mapped[PollValidation | None] = relationship(
        "PollValidation", back_populates="poll", cascade="all, delete-orphan"
    )
    matching_poll: Mapped[Poll | None] = relationship(
        "Poll", remote_side=[id], foreign_keys=[matching_poll_id], post_update=True
    )


class PollResult(Base):
    """Individual party result for a poll."""

    __tablename__ = "poll_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    poll_id: Mapped[int] = mapped_column(ForeignKey("polls.id"))
    party_key: Mapped[str] = mapped_column(ForeignKey("parties.key"))
    percentage: Mapped[float] = mapped_column(Float)

    # Relationships
    poll: Mapped[Poll] = relationship("Poll", back_populates="results")
    party: Mapped[Party] = relationship("Party", back_populates="poll_results")

    __table_args__ = (UniqueConstraint("poll_id", "party_key", name="uix_poll_party"),)


class PollValidation(Base):
    """Persisted validation report for one cleaned poll."""

    __tablename__ = "poll_validations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    poll_id: Mapped[int] = mapped_column(ForeignKey("polls.id"), unique=True, index=True)
    validated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    qc_party_percentage_range: Mapped[bool] = mapped_column(Boolean, nullable=False)
    qc_result_sum_check: Mapped[bool] = mapped_column(Boolean, nullable=False)
    qc_date_consistency: Mapped[bool] = mapped_column(Boolean, nullable=False)
    qc_respondents_plausible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    qc_core_parties_present: Mapped[bool] = mapped_column(Boolean, nullable=False)
    qc_institute_result_jump: Mapped[bool] = mapped_column(Boolean, nullable=False)
    qc_scope_result_jump: Mapped[bool] = mapped_column(Boolean, nullable=False)

    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    poll: Mapped[Poll] = relationship("Poll", back_populates="validation")


class PipelineRun(Base):
    """Audit log for each pipeline:run execution.

    Stores timing, success/failure, and high-level statistics so you can
    review run history without parsing log files.
    """

    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # Unique identifier (UUID string) for cross-referencing with log lines
    run_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)

    # Timing
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)

    # Outcome
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Scraper stats
    scrapers_run: Mapped[int] = mapped_column(Integer, default=0)
    scrapers_succeeded: Mapped[int] = mapped_column(Integer, default=0)
    scrapers_failed: Mapped[int] = mapped_column(Integer, default=0)
    total_scraped_polls: Mapped[int] = mapped_column(Integer, default=0)

    # ETL / cleaner stats
    etl_processed: Mapped[int] = mapped_column(Integer, default=0)
    etl_created: Mapped[int] = mapped_column(Integer, default=0)
    etl_updated: Mapped[int] = mapped_column(Integer, default=0)
    etl_skipped: Mapped[int] = mapped_column(Integer, default=0)
    etl_errors: Mapped[int] = mapped_column(Integer, default=0)

    # Export stats
    export_polls: Mapped[int] = mapped_column(Integer, default=0)
    export_poll_results: Mapped[int] = mapped_column(Integer, default=0)
    export_raw_polls: Mapped[int] = mapped_column(Integer, default=0)

    # Validation stats
    validation_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    validation_total_polls: Mapped[int] = mapped_column(Integer, default=0)
    validation_valid_polls: Mapped[int] = mapped_column(Integer, default=0)
    validation_invalid_polls: Mapped[int] = mapped_column(Integer, default=0)
    validation_warning_polls: Mapped[int] = mapped_column(Integer, default=0)
    validation_valid_share: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Archive (optional)
    archive_created: Mapped[bool] = mapped_column(Boolean, default=False)
    archive_size_mb: Mapped[float | None] = mapped_column(Float, nullable=True)


def _format_public_id(prefix: str, row_id: int) -> str:
    """Return the namespaced public identifier for a database row."""
    return f"{prefix}{row_id:08d}"


@event.listens_for(RawPoll, "after_insert")
def _set_raw_poll_public_id(_mapper, connection, target: RawPoll) -> None:
    """Populate RawPoll.public_id after autoincrement assigned the integer PK."""
    if target.public_id:
        return
    public_id = _format_public_id("R", target.id)
    connection.execute(
        RawPoll.__table__.update()
        .where(RawPoll.__table__.c.id == target.id)
        .values(public_id=public_id)
    )
    target.public_id = public_id


@event.listens_for(Poll, "after_insert")
def _set_poll_public_id(_mapper, connection, target: Poll) -> None:
    """Populate Poll.public_id after autoincrement assigned the integer PK."""
    if target.public_id:
        return
    public_id = _format_public_id("C", target.id)
    connection.execute(
        Poll.__table__.update().where(Poll.__table__.c.id == target.id).values(public_id=public_id)
    )
    target.public_id = public_id
