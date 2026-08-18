"""Pydantic schemas for API requests and responses."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# Raw Poll Schemas
class RawPollBase(BaseModel):
    """Base schema for raw polls."""

    publish_date: str | None = None
    survey_date_start: str | None = None
    survey_date_end: str | None = None
    respondents: str | None = None
    zeitraum: str | None = None
    parties: str | None = None
    institute_id: str | None = None
    provider: str | None = None
    tasker: str | None = None
    source: str | None = None
    scope: str | None = None
    election_id: str | None = None
    method_id: str | None = None
    date_downloaded: str | None = None


class RawPollCreate(RawPollBase):
    """Schema for creating raw polls."""

    pass


class RawPoll(RawPollBase):
    """Schema for raw poll responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    public_id: str | None = None


# Poll Result Schemas
class PollResultBase(BaseModel):
    """Base schema for poll results."""

    party_key: str
    percentage: float


class PollResultCreate(PollResultBase):
    """Schema for creating poll results."""

    pass


class PollResult(PollResultBase):
    """Schema for poll result responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    poll_id: int
    party_name: str | None = None


# Poll Schemas
class PollBase(BaseModel):
    """Base schema for polls."""

    publish_date: dt.date | None = None
    survey_date_start: dt.date | None = None
    survey_date_end: dt.date | None = None
    respondents: int | None = None
    source: str | None = None
    scope: str | None = None
    fingerprint: str | None = None


class PollCreate(PollBase):
    """Schema for creating polls."""

    raw_id: int | None = None
    institute_key: str | None = None
    provider_id: int | None = None
    election_key: str | None = None
    method_key: str | None = None
    date_downloaded: dt.datetime | None = None
    results: list[PollResultCreate] = Field(default_factory=list)


class Poll(PollBase):
    """Schema for poll responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    public_id: str | None = None
    matching_poll_id: int | None = None
    matching_poll_public_id: str | None = None
    matching_status: str | None = None
    institute: str | None = None
    provider: str | None = None
    election: str | None = None
    method: str | None = None
    results: list[PollResult] = Field(default_factory=list)


# Dictionary Schemas
class InstituteBase(BaseModel):
    """Base schema for institutes."""

    name: str
    description: str | None = None


class InstituteCreate(InstituteBase):
    """Schema for creating institutes."""

    pass


class Institute(InstituteBase):
    """Schema for institute responses."""

    model_config = ConfigDict(from_attributes=True)

    key: str


class PartyBase(BaseModel):
    """Base schema for parties."""

    name: str
    short_name: str | None = None
    color: str | None = None
    external_ids: dict[str, str] | None = None


class PartyCreate(PartyBase):
    """Schema for creating parties."""

    pass


class Party(PartyBase):
    """Schema for party responses."""

    model_config = ConfigDict(from_attributes=True)

    key: str


class ProviderBase(BaseModel):
    """Base schema for providers."""

    name: str
    description: str | None = None


class ProviderCreate(ProviderBase):
    """Schema for creating providers."""

    pass


class Provider(ProviderBase):
    """Schema for provider responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int


class ElectionBase(BaseModel):
    """Base schema for elections."""

    election_type: str
    year: int | None = None
    scope: str | None = None
    date: dt.date | None = None
    date_is_estimated: bool | None = None


class ElectionCreate(ElectionBase):
    """Schema for creating elections."""

    pass


class Election(ElectionBase):
    """Schema for election responses."""

    model_config = ConfigDict(from_attributes=True)

    key: str


class MethodBase(BaseModel):
    """Base schema for methods."""

    name: str
    description: str | None = None


class MethodCreate(MethodBase):
    """Schema for creating methods."""

    pass


class Method(MethodBase):
    """Schema for method responses."""

    model_config = ConfigDict(from_attributes=True)

    key: str


# Export Schemas
class ExportData(BaseModel):
    """Schema for exported data."""

    polls: list[Poll]
    metadata: dict[str, Any]


# Scraper Schemas
class ScraperPayload(BaseModel):
    """Schema for scraper payload validation."""

    model_config = ConfigDict(extra="ignore")

    publish_date: str | None = None
    respondents: str | None = None
    zeitraum: str | None = None
    survey_date_start: str | None = None
    survey_date_end: str | None = None
    parties: dict[str, float] | None = None
    institute_id: str | None = None
    provider: str | None = None
    tasker: str | None = None
    source: str | None = None
    scope: str | None = None
    election_id: str | None = None
    method_id: str | None = None
    date_downloaded: str | None = None


# Health Check
class HealthCheck(BaseModel):
    """Health check response schema."""

    status: str
    service: str
    version: str
    release_id: str
    time: dt.datetime
    total_polls: int
    last_run_at: dt.datetime | None = None
    time_since_last_run_seconds: int | None = None
    checks: dict[str, list[dict[str, Any]]]


class ValidationCheck(BaseModel):
    """Result of one data validation check."""

    passed: bool
    severity: Literal["error", "warning"] = "error"
    observed: Any | None = None
    expected: str | None = None
    message: str | None = None
    affected_parties: list[str] = Field(default_factory=list)


class DataValidation(BaseModel):
    """Validation result for one cleaned poll."""

    id: int | None = None
    poll_id: int
    public_id: str | None = None
    validated_at: dt.datetime | None = None

    qc_party_percentage_range: ValidationCheck
    qc_result_sum_check: ValidationCheck
    qc_date_consistency: ValidationCheck
    qc_respondents_plausible: ValidationCheck
    qc_core_parties_present: ValidationCheck
    qc_institute_result_jump: ValidationCheck
    qc_scope_result_jump: ValidationCheck

    valid: bool


class DataValidationSummary(BaseModel):
    """Summary of validation results for a poll collection."""

    total_polls: int
    valid_polls: int
    invalid_polls: int
    warning_polls: int


class DataValidationResponse(BaseModel):
    """Response schema for data validation reports."""

    summary: DataValidationSummary
    items: list[DataValidation] = Field(default_factory=list)


class ValidationCheckSummary(BaseModel):
    """Aggregated pass/fail summary for one validation check."""

    check: str
    passed: int
    failed: int
    needs_review: int | None = None
    pass_share: float


class ValidationFailureSummary(BaseModel):
    """Failure count for one validation check."""

    check: str
    failed: int
    needs_review: int | None = None


class ValidationReport(BaseModel):
    """Aggregate validation quality report."""

    status: Literal["pass", "warn", "fail"]
    public_status: Literal["ready", "review_recommended", "attention_needed"] | None = None
    generated_at: dt.datetime
    total_polls: int
    valid_polls: int
    invalid_polls: int
    warning_polls: int
    research_ready_polls: int | None = None
    polls_outside_quality_criteria: int | None = None
    polls_with_review_notes: int | None = None
    valid_share: float
    invalid_share: float
    warning_share: float
    research_ready_share: float | None = None
    outside_quality_criteria_share: float | None = None
    latest_validated_at: dt.datetime | None = None
    checks: list[ValidationCheckSummary] = Field(default_factory=list)
    top_failure_checks: list[ValidationFailureSummary] = Field(default_factory=list)
    checks_needing_review: list[ValidationFailureSummary] = Field(default_factory=list)
