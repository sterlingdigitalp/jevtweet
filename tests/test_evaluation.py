from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math

import numpy as np
import pytest

from jevtweet import evaluation as ev
from jevtweet.storage import Store


def label_data():
    cutoff = datetime(2025, 6, 1, tzinfo=timezone.utc)
    candidate = {"candidate_id": "target", "candidate_version": 1, "text": "A concrete result with evidence", "author_id": "author",
                 "post_type": "original", "distribution": "organic", "published_at": cutoff.isoformat(),
                 "content_available_at": cutoff.isoformat(), "synthetic": False, "language": "en", "niche": "production_ai_coding"}
    context = {"prediction_cutoff": cutoff.isoformat()}
    candidates, outcomes = [candidate], []
    for i in range(11):
        publication = cutoff - timedelta(days=30 - i * 2)
        history = dict(candidate, candidate_id=f"history-{i}", published_at=publication.isoformat(), content_available_at=publication.isoformat())
        candidates.append(history)
        observed = publication + timedelta(hours=48)
        outcomes.append({"observation_id": f"history-observation-{i}", "candidate_id": history["candidate_id"], "candidate_version": 1,
                         "source": "manual_native_views", "metric": "views", "distribution": "organic", "views": 500 + i * 100,
                         "elapsed_hours": 48, "observed_at": observed.isoformat(), "available_at": observed.isoformat(), "synthetic": False})
    observed = cutoff + timedelta(hours=48)
    outcomes.append({"observation_id": "target-observation", "candidate_id": "target", "candidate_version": 1,
                     "source": "manual_native_views", "metric": "views", "distribution": "organic", "views": 15000,
                     "elapsed_hours": 48, "observed_at": observed.isoformat(), "available_at": observed.isoformat(), "synthetic": False})
    return candidate, context, candidates, outcomes


def test_label_reconstructs_available_baseline_and_separates_events():
    c, context, candidates, outcomes = label_data()
    result = ev.label_candidate(c, context, candidates, outcomes)
    assert result["status"] == "eligible"
    assert result["baseline_sample_count"] == 11
    assert result["baseline_views"] == 1000
    assert result["absolute_reach"] == 1 and result["relative_outperformance"] == 15 and result["label"] == 1
    # Publication alone is insufficient; information availability governs history.
    outcomes[0]["available_at"] = outcomes[-1]["available_at"]
    result = ev.label_candidate(c, context, candidates, outcomes)
    assert result["baseline_sample_count"] == 10 and "history-observation-0" not in result["baseline_observation_ids"]
    outcomes[1]["available_at"] = outcomes[-1]["available_at"]
    assert ev.label_candidate(c, context, candidates, outcomes)["reason"] == "insufficient_as_of_baseline"
    assert ev.label_candidate(c, context, candidates, outcomes, task="absolute_48h_v1")["label"] == 1


def test_pending_missing_zero_baseline_and_wrong_windows_never_become_negative():
    c, context, candidates, outcomes = label_data()
    pending = ev.label_candidate(c, context, candidates, outcomes[:-1], as_of=ev._date(c["published_at"]) + timedelta(hours=12))
    assert pending["status"] == "pending" and pending["label"] is None
    missing = ev.label_candidate(c, context, candidates, outcomes[:-1])
    assert missing["status"] == "unavailable" and missing["label"] is None
    for outcome in outcomes[:-1]:
        outcome["views"] = 0
    assert ev.label_candidate(c, context, candidates, outcomes)["reason"] == "zero_or_missing_baseline"
    outcomes[-1]["metric"] = "impressions"
    assert ev.label_candidate(c, context, candidates, outcomes)["label"] is None
    outcomes[-1]["metric"] = "views"
    outcomes[-1]["elapsed_hours"] = 168
    assert ev.label_candidate(c, context, candidates, outcomes)["label"] is None


def test_baseline_comparability_conflicts_and_postpublication_cutoff():
    c, context, candidates, outcomes = label_data()
    candidates[1]["post_type"] = "reply"
    candidates[2]["distribution"] = "paid"
    assert ev.label_candidate(c, context, candidates, outcomes)["reason"] == "insufficient_as_of_baseline"
    outcomes.append(dict(outcomes[-1], observation_id="conflict", views=2))
    assert ev.label_candidate(c, context, candidates, outcomes)["reason"] == "conflicting_measurements"
    context["prediction_cutoff"] = outcomes[-1]["observed_at"]
    assert ev.label_candidate(c, context, candidates, outcomes)["reason"] == "prediction_after_publication"


def attach_data(store):
    c, context, candidates, outcomes = label_data()
    for candidate in candidates:
        store.put("candidate", ev._key(candidate), candidate)
    for outcome in outcomes:
        store.put("outcome", outcome["observation_id"], outcome)
    j = {"judgment_id": "judgment", "candidate_id": "target", "candidate_version": 1, "created_at": c["published_at"],
         "status": "scored", "score_continuous": 3.5, "score_1_to_5": 4, "execution_mode": "live", "profile_id": "text_core_v1",
         "audience_id": "production_ai_coding", "audience_version": "1", "rubric_version": "rubric_v1",
         "model_requested": "jev-1.13.0", "model_returned": "jev-1.13.0",
         "factors": {name: {"score": 3, "assessability": "assessable"} for name in ev.SEMANTIC_NAMES + ["overall"]}}
    context["historical"] = {"followers": 999999, "observed_at": outcomes[-1]["observed_at"], "available_at": outcomes[-1]["available_at"]}
    store.put("judgment", "judgment", j)
    store.put("run", "judgment", {"judgment_id": "judgment", "request": {"candidate": c, "context": context}})
    return j


def test_attached_outcomes_flow_into_evaluation_eligibility(tmp_path):
    store = Store(tmp_path)
    j = attach_data(store)
    dataset = ev.build_dataset(store)
    assert len(dataset["rows"]) == 1 and dataset["rows"][0]["label"] == 1
    assert dataset["rows"][0]["features"]["log_followers"] is None
    j["execution_mode"] = "mock"
    store.put("judgment", "judgment", j, replace=True)
    assert ev.eligibility(store)["eligible_rows"] == 0
    assert "mock_cannot_establish_real_predictive_evidence" in ev.eligibility(store)["exclusion_counts"]


def test_splits_purge_forward_duplicates_threads_and_immature_labels():
    rows = ev._synthetic_rows(200)
    rows[102]["text"] = rows[12]["text"]
    rows[170]["thread_id"] = rows[14]["thread_id"] = "same-thread"
    rows[99]["label_available_at"] = rows[105]["cutoff"]
    rows[140]["references"] = [{"candidate_id": rows[180]["candidate_id"], "available_at": rows[120]["cutoff"], "split": "development"}]
    splits, manifest = ev.split_dataset(rows)
    reasons = {r["key"]: r["reason"] for r in manifest["removals"]}
    assert reasons[rows[102]["key"]] == "near_duplicate_or_thread_cross_partition"
    assert reasons[rows[170]["key"]] == "near_duplicate_or_thread_cross_partition"
    assert reasons[rows[99]["key"]] == "not_mature_at_next_boundary"
    assert reasons[rows[140]["key"]] == "reference_split_or_time_leakage"
    assert not ({r["author"] for r in splits["train"]} & set(manifest["heldout_authors"]))


@pytest.fixture(scope="module")
def synthetic_report(tmp_path_factory):
    store = Store(tmp_path_factory.mktemp("evaluation"))
    return store, ev.evaluate(store, synthetic=True)


def test_executable_models_calibration_metrics_and_exact_replay(synthetic_report):
    store, report = synthetic_report
    assert report["status"] == "evaluated" and set(report["metrics"]) == set(ev.METHODS)
    assert report["predictive_validation"] == "not_established"
    train = set(report["split_manifest"]["partitions"]["train"])
    selection = set(report["split_manifest"]["partitions"]["selection"])
    calibration = set(report["split_manifest"]["partitions"]["calibration"])
    test = set(report["split_manifest"]["partitions"]["test"])
    for name, model in report["models"].items():
        assert not set(model["fitted_on"]) & (calibration | test)
        if "calibrator" in model:
            assert set(model["calibrator"]["fitted_on"]) == calibration
            assert not set(model["calibrator"]["fitted_on"]) & (train | selection | test)
    assert report["uncertainty"]["intervals"]["paired_brier_improvement_vs_metadata"]
    assert report["subgroups"]["niche"]
    assert ev.evaluate(store, synthetic=True)["experiment_id"] == report["experiment_id"]
    assert len(store.list("experiment")) == 1


def test_synthetic_never_promotes_and_rejections_are_audited(synthetic_report):
    store, report = synthetic_report
    attempted = ev.promote(store, report["experiment_id"], approved_by="test-human", rationale="Exercise rejection")
    assert not attempted["forecast_available"]
    assert "synthetic_evidence_cannot_promote" in attempted["promotion"]["reasons"]
    assert attempted["audit"][-1]["accepted"] is False
    assert ev.forecast_status(store)["available"] is False
    assert ev.predict(store, "unused")["breakout_probability"] is None


def test_real_holdout_cannot_be_reopened_even_with_new_id(tmp_path, monkeypatch):
    rows = ev._synthetic_rows(160)
    for r in rows:
        r["synthetic"] = False
        r["execution_mode"] = "live"
    data = {"rows": rows, "exclusions": [], "labels": [], "total_candidates": len(rows), "coverage": 1,
            "exclusion_counts": {}, "configuration_signatures": []}
    monkeypatch.setattr(ev, "build_dataset", lambda *args, **kwargs: deepcopy(data))
    monkeypatch.setattr(ev, "_bootstrap", lambda *args, **kwargs: {"clusters": 40, "intervals": {}})
    store = Store(tmp_path)
    first = ev.evaluate(store)
    assert first["status"] == "evaluated"
    second = ev.evaluate(store, holdout_id="new-name-does-not-make-new-data")
    assert second["status"] == "holdout_already_consumed" and not second["metrics"]
    assert len(store.list("holdout")) == 1


def test_undefined_metrics_and_fold_local_preprocessing():
    assert ev.metrics([], [])["brier"] is None
    assert ev.metrics([0, 0], [.1, .2])["roc_auc"] is None
    rows = ev._synthetic_rows(40)
    model = ev.fit_model(rows[:20], ev.METADATA_NAMES)
    mean_before = deepcopy(model["means"])
    rows[30]["features"]["log_followers"] = 1e9
    ev.raw_predict(model, rows[20:])
    assert model["means"] == mean_before
    assert all(math.isfinite(x) for x in model["medians"])
