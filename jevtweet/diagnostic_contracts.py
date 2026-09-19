"""Private diagnostic sidecars; intentionally separate from historical outcomes."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from .contracts import Record, now


class DiagnosticRestriction(Record):
    research_role: Literal["diagnostic_only"] = "diagnostic_only"
    sampling_status: Literal["unknown"] = "unknown"
    outcome_window: Literal["unknown"] = "unknown"
    eligible_for_calibration: Literal[False] = False
    eligible_for_final_test: Literal[False] = False
    eligible_for_promotion: Literal[False] = False


class MetricSnapshot(Record):
    snapshot_id: str
    source_id: str
    candidate_id: str
    candidate_version: int = 1
    imported_at: datetime
    observed_at: None = None
    elapsed_hours: None = None
    window_status: Literal["unknown"] = "unknown"
    views: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)
    reposts: int | None = Field(default=None, ge=0)
    quotes: int | None = Field(default=None, ge=0)
    replies: int | None = Field(default=None, ge=0)
    bookmarks: int | None = Field(default=None, ge=0)
    supplied_engagement_score: int | None = Field(default=None, ge=0)


class DiagnosticSourceRecord(Record):
    source_id: str
    candidate_id: str
    source_record_index: int
    raw: dict[str, str]
    source_calendar_date: str | None
    source_date_precision: Literal["calendar_date_only", "unknown"]
    source_timezone: None = None
    imported_at: datetime
    post_type: str
    media_presence: Literal["yes", "no", "unknown"]
    initial_cohort_eligible: bool
    limitations: list[str]
    restrictions: DiagnosticRestriction = Field(default_factory=DiagnosticRestriction)


class DiagnosticAuthorization(Record):
    protocol_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    approved_by: str = Field(min_length=1)
    approval_note: str = Field(min_length=1)
    approved_at: datetime = Field(default_factory=now)
    pilot_ceiling_usd: float = Field(gt=0)
    authorization_scope: Literal["fixed_diagnostic_first_judgments"] = "fixed_diagnostic_first_judgments"
