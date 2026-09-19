"""Planning estimates use development evidence, never the protected final outcomes."""

import pytest

from jevtweet import evaluation as ev
from jevtweet.storage import Store


def test_rare_events_exceed_cap_and_exclusions_increase_collection(tmp_path, monkeypatch):
    from jevtweet.readiness import development_readiness

    rows = ev._synthetic_rows(100)
    for i, row in enumerate(rows):
        row["label"] = int(i == 0)
    monkeypatch.setattr(
        ev,
        "prepare_development",
        lambda *a, **kw: {
            "rows": rows,
            "total_candidates": 250,
            "development_candidate_count": 200,
            "exclusions": [{"reason": "failed_execution"}] * 100,
            "exclusion_counts": {"failed_execution": 100},
            "partition_manifest": {"partitions": {"test": ["do-not-read"]}},
        },
    )
    store = Store(tmp_path)
    report = development_readiness(store)
    assert report["test_outcomes_inspected"] is False
    assert report["observed"]["event_frequency"] == 0.01
    assert report["observed"]["retention_rate"] == 0.5
    assert report["collection_estimates"]["eligible_rows_at_observed_frequency"] >= 20_000
    assert report["collection_estimates"]["candidate_rows_at_observed_frequency"] >= 40_000
    assert report["evaluation_cap"]["exceeds_cap_at_observed_frequency"] is True
    assert report["evaluation_cap"]["expected_test_positives_at_cap"] == 10
    assert store.list("holdout") == []
    assert store.list("experiment") == []


@pytest.mark.parametrize("positives", [0, 100])
def test_zero_observed_class_has_no_fabricated_finite_collection_target(tmp_path, monkeypatch, positives):
    from jevtweet.readiness import development_readiness

    rows = ev._synthetic_rows(100)
    for row in rows:
        row["label"] = int(bool(positives))
    monkeypatch.setattr(
        ev,
        "prepare_development",
        lambda *a, **kw: {"rows": rows, "development_candidate_count": 100},
    )
    report = development_readiness(Store(tmp_path))
    assert report["collection_estimates"]["eligible_rows_at_observed_frequency"] is None
    assert report["status"] == "insufficient_development_evidence"
    assert report["predictive_validation"] == "not_established"


def test_empty_readiness_exposes_no_final_test_evidence(tmp_path):
    from jevtweet.readiness import development_readiness

    report = development_readiness(Store(tmp_path))
    assert report["observed"]["event_frequency"] is None
    assert report["collection_estimates"]["candidate_rows_at_observed_frequency"] is None
    assert report["evaluation_cap"]["minimum_test_event_frequency_at_cap"] == 0.04
    assert report["test_outcomes_inspected"] is False


def test_repeated_and_unknown_authors_do_not_create_independent_support(tmp_path, monkeypatch):
    from jevtweet.readiness import development_readiness

    rows = ev._synthetic_rows(100)
    for i, row in enumerate(rows):
        row.update(author="one-author" if i < 80 else f"unknown:{i}", author_known=i < 80)
    monkeypatch.setattr(
        ev,
        "prepare_development",
        lambda *a, **kw: {"rows": rows, "development_candidate_count": 100},
    )
    report = development_readiness(Store(tmp_path))
    observed = report["observed"]
    assert observed["known_authors"] == observed["independent_author_clusters"] == 1
    assert observed["unknown_author_rows"] == 20
    assert report["collection_estimates"]["author_support_shortfall_vs_test_requirement"] == 19


def test_intended_row_cap_accounts_for_exclusions_not_just_successful_rows(tmp_path, monkeypatch):
    from jevtweet.readiness import development_readiness

    rows = ev._synthetic_rows(100)
    for index, row in enumerate(rows):
        row["label"] = int(index < 10)
    monkeypatch.setattr(
        ev,
        "prepare_development",
        lambda *a, **kw: {"rows": rows, "development_candidate_count": 250},
    )
    report = development_readiness(Store(tmp_path))
    assert report["collection_estimates"]["eligible_rows_at_observed_frequency"] <= 5000
    assert report["collection_estimates"]["candidate_rows_at_observed_frequency"] > 5000
    assert report["evaluation_cap"]["exceeds_cap_at_observed_frequency"] is True
