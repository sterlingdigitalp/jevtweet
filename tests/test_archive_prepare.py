"""Invented integration cases; collection metadata must never become Jev state."""

from datetime import datetime, timezone

from jevtweet.contracts import Candidate, PredictionContext
from jevtweet.state import audience_by_id, build_state

AT = datetime(2026, 1, 20, tzinfo=timezone.utc)


def test_nested_collector_metadata_never_enters_judgment_state():
    from jevtweet.archive_prepare import build_archive_records

    raw = {
        "post_id": "invented-target",
        "text": "I reduced 48 requests to 2.",
        "post_type": "quote",
        "has_media": False,
        "author_handle": "invented",
        "created_at_utc": "2026-01-01T00:00:00Z",
        "views": 87654321,
        "author": {"followers": 99999},
        "metrics": {"likes": 88888},
    }
    evidence = {
        "post_id": "invented-quote",
        "text": "Original measurements: 48 requests.",
        "published_at": None,
        "available_at": AT.isoformat(),
        "timestamp_basis": "ingestion_observation_publication_unknown",
        "source_version_id": "v2",
        "media_completeness": "unknown_supplied_text_only",
        "metrics": {"views": 654321},
        "rank": 999,
    }
    parent = dict(evidence, post_id="invented-parent", text="The separate parent.")
    bundle = {
        "source_id": "invented-source",
        "targets": [
            {
                "post_id": raw["post_id"],
                "selected": raw,
                "selected_metric": {"views": 87654321},
                "versions": [],
                "field_resolution": {},
            }
        ],
    }
    resolved = {
        "records": [
            {
                "post_id": raw["post_id"],
                "panel": "quote",
                "status": "included",
                "reason_codes": [],
                "quoted_evidence": evidence,
                "parent_evidence": parent,
            }
        ]
    }
    records = build_archive_records(bundle, resolved, assessment_at=AT)
    candidate = Candidate.model_validate(records["candidates"][0])
    context = PredictionContext.model_validate(records["contexts"][0]["context"])
    state = build_state(candidate, context, audience_by_id("production_ai_coding"))
    assert state["candidate"]["text"] == raw["text"]
    assert state["candidate"]["quoted"]["text"] == evidence["text"]
    assert state["candidate"]["parent"] is None
    assert "The separate parent." in state["topic"]["text"]
    assert state["historical"] is None and state["references"] == []
    assert candidate.published_at is None and candidate.author_id is None
    forbidden = {"metrics", "views", "likes", "rank", "followers", "author", "source_version_id"}

    def check(value):
        if isinstance(value, dict):
            assert not forbidden.intersection(value)
            for nested in value.values():
                check(nested)
        elif isinstance(value, list):
            for nested in value:
                check(nested)

    check(state)
    assert records["metric_snapshots"][0]["views"] == 87654321
    assert records["metric_snapshots"][0]["observed_at"] is None
    assert records["metric_snapshots"][0]["supplied_engagement_score"] is None
    assert records["groups"][candidate.candidate_id] == {
        "panel": "quote",
        "account": "invented",
        "month": "2026-01",
    }


def test_missing_text_is_accounted_without_fabricated_candidate():
    from jevtweet.archive_prepare import build_archive_records

    bundle = {
        "source_id": "invented-source",
        "targets": [
            {
                "post_id": "missing",
                "selected": {"post_type": "quote", "text": None, "has_media": False},
                "selected_metric": None,
                "versions": [],
                "field_resolution": {},
            }
        ],
    }
    resolved = {
        "records": [
            {
                "post_id": "missing",
                "panel": "quote",
                "status": "source_incomplete",
                "reason_codes": ["target_text_missing"],
                "quoted_evidence": None,
                "parent_evidence": None,
            }
        ]
    }
    result = build_archive_records(bundle, resolved, assessment_at=AT)
    assert result["candidates"] == []
    assert len(result["normalized_source"]) == 1
    assert not result["normalized_source"][0]["initial_cohort_eligible"]
    assert result["metric_snapshots"][0]["views"] is None


def test_metric_join_uses_normalized_counts_without_coercing_blanks_to_zero():
    from jevtweet.archive_prepare import build_archive_records

    raw = {
        "post_id": "count-target",
        "text": "An invented full sentence.",
        "post_type": "original",
        "has_media": False,
    }
    bundle = {
        "source_id": "invented-source",
        "targets": [
            {
                "post_id": "count-target",
                "selected": raw,
                "selected_metric": {"views": "1,234", "likes": ""},
                "versions": [],
                "field_resolution": {},
                "metric_resolution": {"normalized_counts": {"views": 1234, "likes": None}},
            }
        ],
    }
    resolution = {
        "records": [
            {
                "post_id": "count-target",
                "panel": "original",
                "status": "included",
                "reason_codes": [],
                "quoted_evidence": None,
                "parent_evidence": None,
            }
        ]
    }
    result = build_archive_records(bundle, resolution, assessment_at=AT)
    assert result["metric_snapshots"][0]["views"] == 1234
    assert result["metric_snapshots"][0]["likes"] is None


def test_numeric_source_identifiers_receive_permanent_restrictions(tmp_path):
    from jevtweet.archive_prepare import _register_archive
    from jevtweet.research_restrictions import restricted_candidates
    from jevtweet.service import Service
    from jevtweet.settings import Settings

    service = Service(Settings(data_dir=tmp_path / "data", account_dir=tmp_path / "account"))
    bundle = {
        "source_id": "invented-numeric-source",
        "targets": [],
        "versions": [
            {
                "record": {
                    "post_id": "invented-target",
                    "text": "A complete invented target.",
                    "quoted_id": 42,
                    "quoted_text": "Distinct invented source text.",
                    "parent_id": 43,
                }
            },
            {"record": {"context_id": 44, "text": "Invented standalone source."}},
        ],
    }
    _register_archive(service.store, bundle)
    candidates = [
        Candidate(candidate_id=str(identity), text="Changed wording without shared content.")
        for identity in (42, 43, 44)
    ]
    assert set(restricted_candidates(service.store, candidates)) == {"42:1", "43:1", "44:1"}


def test_article_source_and_wrapper_identities_remain_diagnostic_only(tmp_path):
    from jevtweet.archive_prepare import _register_archive
    from jevtweet.research_restrictions import restricted_candidates
    from jevtweet.service import Service
    from jevtweet.settings import Settings

    service = Service(Settings(data_dir=tmp_path / "data", account_dir=tmp_path / "account"))
    bundle = {
        "source_id": "invented-article-source",
        "targets": [],
        "versions": [
            {
                "record": {
                    "article_id": "invented-article",
                    "wrapper_post_id": "invented-wrapper",
                    "opening_snippet": "An invented partial article opening.",
                    "full_body_missing": True,
                }
            }
        ],
    }
    _register_archive(service.store, bundle)
    candidates = [
        Candidate(candidate_id=identity, text="Unrelated changed wording.")
        for identity in ("invented-article", "invented-wrapper")
    ]
    assert set(restricted_candidates(service.store, candidates)) == {
        "invented-article:1",
        "invented-wrapper:1",
    }
