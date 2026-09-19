"""Canonical v1 contracts. Only the integration lead changes this module."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def now() -> datetime:
    return datetime.now(timezone.utc)


def uid() -> str:
    return str(uuid4())


def canonical(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: Literal["1"] = "1"

    @field_validator("*", mode="after")
    @classmethod
    def utc(cls, v: Any) -> Any:
        if isinstance(v, datetime):
            if v.tzinfo is None:
                raise ValueError("timestamps must include a timezone")
            return v.astimezone(timezone.utc)
        return v


class EvidenceText(Record):
    text: str = Field(max_length=12000)
    occurred_at: datetime
    available_at: datetime
    provenance: str = "manual"


class Media(Record):
    kind: Literal["none", "image", "video", "audio", "other"] = "none"
    essential: bool = False
    description: EvidenceText | None = None


class Candidate(Record):
    candidate_id: str = Field(default_factory=uid, min_length=1, max_length=128)
    candidate_version: int = Field(default=1, ge=1)
    text: str = Field(max_length=16000)
    language: str = "en"
    post_type: Literal["original", "reply", "quote", "thread"] = "original"
    author_id: str | None = None
    published_at: datetime | None = None
    content_available_at: datetime = Field(default_factory=now)
    parent: EvidenceText | None = None
    quoted: EvidenceText | None = None
    media: Media = Field(default_factory=Media)
    thread_id: str | None = None
    niche: str = "production_ai_coding"
    distribution: Literal["organic", "paid", "giveaway", "unknown"] = "unknown"
    provenance: str = "manual"
    synthetic: bool = False


class Audience(Record):
    audience_id: str
    version: str
    description: str
    interests: list[str]
    assumed_knowledge: list[str]
    examples: list[str]
    assumptions: list[str] = Field(default_factory=lambda: ["Seed persona; not validated follower research."])


class HistoricalMetadata(Record):
    followers: int | None = Field(default=None, ge=0)
    normal_48h_views: float | None = Field(default=None, ge=0)
    baseline_sample_count: int = Field(default=0, ge=0)
    observed_at: datetime
    available_at: datetime
    provenance: str


class Reference(Record):
    candidate_id: str
    candidate_version: int = 1
    text: str = Field(max_length=16000)
    published_at: datetime
    available_at: datetime
    split: Literal["train", "development", "prospective", "test"] = "development"
    thread_id: str | None = None


class PredictionContext(Record):
    prediction_cutoff: datetime = Field(default_factory=now)
    historical: HistoricalMetadata | None = None
    topic: EvidenceText | None = None
    references: list[Reference] = Field(default_factory=list, max_length=10)
    evaluation_split: Literal["train", "development", "prospective", "test"] = "prospective"
    reference_rule: str = "keyword_overlap_v1; earlier available text; no target/near-duplicate/thread/test"


class JudgeRequest(Record):
    candidate: Candidate
    context: PredictionContext = Field(default_factory=PredictionContext)
    audience_id: str = "production_ai_coding"
    profile_id: Literal["text_core_v1", "reference_enriched_v1"] = "text_core_v1"
    execution_mode: Literal["live", "mock"] = "mock"


class Factor(Record):
    question_id: str
    type: Literal["score", "choice", "noul"]
    assessability: Literal["assessable", "not_assessable", "unknown"] = "assessable"
    score: float | None = Field(default=None, ge=0, le=4)
    choice: str | None = None
    noul: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    probabilities: dict[str, float] = Field(default_factory=dict)
    legend: dict[str, str] = Field(default_factory=dict)
    evidence_references: list[str] = Field(default_factory=list)
    error: str | None = None

    @model_validator(mode="after")
    def no_noul_confidence(self) -> Factor:
        if self.type == "noul" and self.confidence is not None:
            raise ValueError("Noul has no separate confidence")
        return self


class ProviderResult(Record):
    factors: dict[str, Factor] = Field(default_factory=dict)
    model_returned: str | None = None
    usage: dict[str, int | None] = Field(default_factory=dict)
    error_category: str | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class Judgment(Record):
    judgment_id: str = Field(default_factory=uid)
    candidate_id: str
    candidate_version: int
    status: Literal["scored", "partial", "abstained", "failed"]
    execution_mode: Literal["live", "mock"]
    mode: Literal["editorial", "forecast"] = "editorial"
    profile_id: str
    audience_id: str
    audience_version: str
    rubric_version: str
    score_continuous: float | None = None
    score_1_to_5: int | None = None
    quality: float | None = None
    risk_penalty: float | None = None
    breakout_probability: float | None = None
    label_definition_id: str | None = None
    predictor_id: str | None = None
    calibration_status: str = "not_established"
    evidence_completeness: Literal["complete", "partial", "unknown"] = "unknown"
    execution_status: Literal["succeeded", "partial", "failed", "not_attempted"] = "not_attempted"
    factors: dict[str, Factor] = Field(default_factory=dict)
    review_flags: list[str] = Field(default_factory=list)
    explanation: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    model_requested: str
    model_returned: str | None = None
    sdk_version: str = "0.7.0"
    input_hash: str
    reference_set_hash: str
    code_commit: str
    created_at: datetime = Field(default_factory=now)
    completed_at: datetime | None = None
    usage: dict[str, int | None] = Field(default_factory=dict)
    estimated_cost: float = 0
    cost_is_estimate: bool = True
    latency: float | None = None
    attempt_count: int = 0
    error_category: str | None = None
    cached: bool = False


class OutcomeObservation(Record):
    observation_id: str = Field(default_factory=uid)
    candidate_id: str
    candidate_version: int = 1
    source: str
    metric: Literal["views", "impressions"] = "views"
    observed_at: datetime
    available_at: datetime
    elapsed_hours: float = Field(ge=0)
    views: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)
    reposts: int | None = Field(default=None, ge=0)
    replies: int | None = Field(default=None, ge=0)
    distribution: Literal["organic", "paid", "giveaway", "unknown"] = "unknown"
    provenance: str = "manual"
    synthetic: bool = False


class Annotation(Record):
    annotation_id: str = Field(default_factory=uid)
    candidate_id: str
    candidate_version: int = 1
    rating: int = Field(ge=1, le=5)
    annotator: str
    created_at: datetime = Field(default_factory=now)
    note: str = ""


class ImportRequest(Record):
    content: str = Field(max_length=10_000_000)
    format: Literal["csv", "jsonl"] = "jsonl"
    mapping: dict[str, str] = Field(default_factory=dict)
    preview: bool = False


class JobRequest(Record):
    candidate_ids: list[str] = Field(min_length=1, max_length=1000)
    audience_id: str = "production_ai_coding"
    profile_id: Literal["text_core_v1", "reference_enriched_v1"] = "text_core_v1"
    execution_mode: Literal["mock", "live"] = "mock"


class CompareRequest(Record):
    requests: list[JudgeRequest] = Field(min_length=2, max_length=8)


class EvaluationRequest(Record):
    synthetic: bool = False
    task: Literal["breakout_48h_v1", "absolute_48h_v1"] = "breakout_48h_v1"
    representative_sampling: bool = False
    comparison_population: str = ""
    cohort: dict[str, str] | None = None


class DiscoveryRequest(Record):
    experiment_id: str
    proposals: list[dict[str, Any]] = Field(min_length=1, max_length=8)
    cost_limit_usd: float = Field(gt=0)
    max_rows: int = Field(default=100, ge=24, le=1000)
    max_requests: int = Field(default=100, ge=1, le=8000)
    live: bool = False


class PromotionRequest(Record):
    approved_by: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class ExperimentComparisonRequest(Record):
    experiment_ids: list[str] = Field(min_length=2, max_length=8)
