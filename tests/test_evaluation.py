import math
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from jevtweet import evaluation as ev
from jevtweet.storage import Store


def label_data():
    cutoff = datetime(2025, 6, 1, tzinfo=timezone.utc)
    candidate = {
        "candidate_id": "target",
        "candidate_version": 1,
        "text": "A concrete result with evidence",
        "author_id": "author",
        "post_type": "original",
        "distribution": "organic",
        "published_at": cutoff.isoformat(),
        "content_available_at": cutoff.isoformat(),
        "synthetic": False,
        "language": "en",
        "niche": "production_ai_coding",
    }
    context = {"prediction_cutoff": cutoff.isoformat()}
    candidates, outcomes = [candidate], []
    for i in range(11):
        publication = cutoff - timedelta(days=30 - i * 2)
        history = dict(
            candidate,
            candidate_id=f"history-{i}",
            published_at=publication.isoformat(),
            content_available_at=publication.isoformat(),
        )
        candidates.append(history)
        observed = publication + timedelta(hours=48)
        outcomes.append(
            {
                "observation_id": f"history-observation-{i}",
                "candidate_id": history["candidate_id"],
                "candidate_version": 1,
                "source": "manual_native_views",
                "metric": "views",
                "distribution": "organic",
                "views": 500 + i * 100,
                "elapsed_hours": 48,
                "observed_at": observed.isoformat(),
                "available_at": observed.isoformat(),
                "synthetic": False,
            }
        )
    observed = cutoff + timedelta(hours=48)
    outcomes.append(
        {
            "observation_id": "target-observation",
            "candidate_id": "target",
            "candidate_version": 1,
            "source": "manual_native_views",
            "metric": "views",
            "distribution": "organic",
            "views": 15000,
            "elapsed_hours": 48,
            "observed_at": observed.isoformat(),
            "available_at": observed.isoformat(),
            "synthetic": False,
        }
    )
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
    assert (
        result["baseline_sample_count"] == 10
        and "history-observation-0" not in result["baseline_observation_ids"]
    )
    outcomes[1]["available_at"] = outcomes[-1]["available_at"]
    assert ev.label_candidate(c, context, candidates, outcomes)["reason"] == "insufficient_as_of_baseline"
    assert ev.label_candidate(c, context, candidates, outcomes, task="absolute_48h_v1")["label"] == 1


def test_pending_missing_zero_baseline_and_wrong_windows_never_become_negative():
    c, context, candidates, outcomes = label_data()
    pending = ev.label_candidate(
        c, context, candidates, outcomes[:-1], as_of=ev._date(c["published_at"]) + timedelta(hours=12)
    )
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
    j = {
        "judgment_id": "judgment",
        "candidate_id": "target",
        "candidate_version": 1,
        "created_at": c["published_at"],
        "status": "scored",
        "score_continuous": 3.5,
        "score_1_to_5": 4,
        "execution_mode": "live",
        "profile_id": "text_core_v1",
        "audience_id": "production_ai_coding",
        "audience_version": "1",
        "rubric_version": ev.RUBRIC["version"],
        "schema_version": "1",
        "provenance": {"rubric_hash": ev.digest(ev.RUBRIC)},
        "model_requested": "jev-1.13.0",
        "model_returned": "jev-1.13.0",
        "factors": {
            name: {"score": 3, "assessability": "assessable"} for name in ev.SEMANTIC_NAMES + ["overall"]
        },
    }
    context["historical"] = {
        "followers": 999999,
        "observed_at": outcomes[-1]["observed_at"],
        "available_at": outcomes[-1]["available_at"],
    }
    run = {
        "judgment_id": "judgment",
        "request": {
            "candidate": c,
            "context": context,
            "execution_mode": "live",
            "profile_id": j["profile_id"],
            "audience_id": j["audience_id"],
        },
        "state": {"candidate": {"text": c["text"]}},
        "questions": ev.RUBRIC["questions"],
    }
    j["sdk_version"] = "0.7.0"
    j["input_hash"] = ev.digest(
        {
            "state": run["state"],
            "questions": run["questions"],
            "rubric": ev.RUBRIC,
            "model": j["model_requested"],
            "sdk": j["sdk_version"],
            "profile": j["profile_id"],
            "mode": j["execution_mode"],
        }
    )
    store.put("judgment", "judgment", j)
    store.put("run", "judgment", run)
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
    rows[140]["references"] = [
        {
            "candidate_id": rows[180]["candidate_id"],
            "available_at": rows[120]["cutoff"],
            "split": "development",
        }
    ]
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
    attempted = ev.promote(
        store, report["experiment_id"], approved_by="test-human", rationale="Exercise rejection"
    )
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
    data = {
        "rows": rows,
        "exclusions": [],
        "labels": [],
        "total_candidates": len(rows),
        "coverage": 1,
        "exclusion_counts": {},
        "configuration_signatures": [],
    }
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
    assert ev.metrics([0, 0], [0.1, 0.2])["roc_auc"] is None
    rows = ev._synthetic_rows(40)
    model = ev.fit_model(rows[:20], ev.METADATA_NAMES)
    mean_before = deepcopy(model["means"])
    rows[30]["features"]["log_followers"] = 1e9
    ev.raw_predict(model, rows[20:])
    assert model["means"] == mean_before
    assert all(math.isfinite(x) for x in model["medians"])


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ({"model_returned": "jev-latest"}, "pinned_model_identity_mismatch"),
        (
            {"model_requested": "unvalidated-model", "model_returned": "unvalidated-model"},
            "pinned_model_identity_mismatch",
        ),
        ({"rubric_version": "unknown-version"}, "unverified_rubric_signature"),
        ({"provenance": {"rubric_hash": "wrong"}}, "unverified_rubric_signature"),
        ({"schema_version": "future-v2"}, "unsupported_record_schema"),
        ({"input_hash": "unrelated-run"}, "prediction_run_lineage_mismatch"),
    ],
)
def test_eligibility_rejects_unverified_model_rubric_schema(tmp_path, mutation, reason):
    store = Store(tmp_path)
    judgment = attach_data(store)
    judgment.update(mutation)
    store.put("judgment", "judgment", judgment, replace=True)
    result = ev.eligibility(store)
    assert result["eligible_rows"] == 0 and result["exclusion_counts"][reason] == 1


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("sampling_provenance", "winners_only", "sampling_provenance_must_be_documented"),
        ("sampling_provenance", "balanced", "sampling_provenance_must_be_documented"),
        ("sampling_provenance", "manual", "sampling_provenance_must_be_documented"),
        ("sampling_provenance", "unknown", "sampling_provenance_must_be_documented"),
        ("model_returned", "jev-latest", "verified_live_pinned_model_required"),
        ("rubric_hash", "wrong-signature", "verified_schema_and_rubric_required"),
        ("schema_version", "v2", "verified_schema_and_rubric_required"),
        ("synthetic", True, "full_cohort_real_evidence_required"),
    ],
)
def test_promotion_checks_test_lineage_not_only_development(synthetic_report, field, value, reason):
    _, original = synthetic_report
    report = deepcopy(original)
    report["synthetic"] = False
    for partition in report["cohort_audit"].values():
        for row in partition:
            row.update(
                sampling_provenance="prospective_enrollment_v1",
                execution_mode="live",
                synthetic=False,
                model_requested=ev.Settings.model,
                model_returned=ev.Settings.model,
            )
    report["cohort_audit_hash"] = ev.digest(report["cohort_audit"])
    report["frozen_candidate_hash"] = ev.digest(ev._frozen_payload(report))
    assert reason not in ev._promotion_gates(report)["reasons"]
    report["cohort_audit"]["test"][0][field] = value
    report["cohort_audit_hash"] = ev.digest(report["cohort_audit"])
    report["frozen_candidate_hash"] = ev.digest(ev._frozen_payload(report))
    assert reason in ev._promotion_gates(report)["reasons"]


def test_cohort_audit_cannot_omit_test_or_change_after_freeze(synthetic_report):
    _, original = synthetic_report
    report = deepcopy(original)
    report["cohort_audit"]["test"] = []
    assert "full_frozen_cohort_audit_required" in ev._promotion_gates(report)["reasons"]
    report = deepcopy(original)
    report["models"]["jev_plus_metadata"]["coefficients"][0] += 0.5
    assert "frozen_candidate_integrity_required" in ev._promotion_gates(report)["reasons"]


def test_holdout_identity_blocks_rekey_version_and_near_duplicate():
    row = ev._synthetic_rows(1)[0]
    old = ev._holdout_identity(row)
    assert ev._overlaps_holdout([ev._holdout_identity(dict(row, key="synthetic-0:2"))], [old])
    rekeyed = dict(row, key="new-id:1", candidate_id="new-id")
    assert ev._overlaps_holdout([ev._holdout_identity(rekeyed)], [old])
    rekeyed["text"] += " extra"
    assert ev._overlaps_holdout([ev._holdout_identity(rekeyed)], [old])
    unrelated = ev._synthetic_rows(2)[1]
    assert not ev._overlaps_holdout([ev._holdout_identity(unrelated)], [old])


def test_forged_approval_without_originating_experiment_cannot_enable_forecast(tmp_path):
    store = Store(tmp_path)
    store.put("predictor", "orphan", {"predictor_id": "orphan", "approval": {"accepted": True}})
    result = ev.predict(store, "unused")
    assert not result["available"] and "originating evaluation" in result["reason"]
    assert not ev.forecast_status(store)["available"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", "2"),
        ("rubric_version", "future-rubric"),
        ("rubric_hash", "changed-criteria"),
        ("model_requested", "jev-latest"),
    ],
)
def test_prospective_configuration_mismatch_is_unavailable(tmp_path, monkeypatch, field, value):
    store = Store(tmp_path)
    judgment = attach_data(store)
    configuration = {
        k: judgment[k]
        for k in (
            "profile_id",
            "audience_id",
            "audience_version",
            "rubric_version",
            "schema_version",
            "model_requested",
        )
    }
    configuration["rubric_hash"] = judgment["provenance"]["rubric_hash"]
    store.put(
        "predictor",
        "unit-test",
        {"predictor_id": "unit-test", "approval": {"accepted": True}, "configuration": configuration},
    )
    # Isolate prospective configuration checks; separate tests exercise gates
    # and originating-artifact validation. This is not a promotable experiment.
    monkeypatch.setattr(ev, "_predictor_lineage_errors", lambda *args: [])
    if field == "rubric_hash":
        judgment["provenance"][field] = value
    else:
        judgment[field] = value
    store.put("judgment", "judgment", judgment, replace=True)
    result = ev.predict(store, "judgment")
    assert not result["available"] and result["breakout_probability"] is None


def test_changed_model_artifact_rejected_against_origin(synthetic_report, tmp_path, monkeypatch):
    _, original = synthetic_report
    store = Store(tmp_path)
    report = deepcopy(original)
    report["promotion"] = {"approved": True, "approval": {"accepted": True}}
    store.put("experiment", report["experiment_id"], report)
    # Isolate artifact equality from the independent evidence-gate checks.
    monkeypatch.setattr(ev, "_promotion_gates", lambda *args: {"eligible": True})
    predictor = {
        "predictor_id": report["experiment_id"],
        "frozen_candidate_hash": report["frozen_candidate_hash"],
        "model": deepcopy(report["models"]["jev_plus_metadata"]),
    }
    predictor["model"]["coefficients"][0] += 0.1
    assert "differs from the approved frozen evaluation" in ev._predictor_lineage_errors(store, predictor)[0]


def test_synthetic_fixture_uses_authoritative_editorial_scorer(monkeypatch):
    from jevtweet import scoring

    calls = []

    def authoritative(factors, profile_id, **kwargs):
        assert profile_id == "text_core_v1"
        assert factors["assessability"].choice == "assessable"
        assert factors["missing_context"].noul == 0
        assert factors["instruction_like"].noul == 0
        calls.append(factors)
        return {"status": "scored", "score_continuous": 2.875}

    monkeypatch.setattr(scoring, "score", authoritative)
    rows = ev._synthetic_rows(3)
    assert len(calls) == 3 and all(row["editorial"] == 2.875 for row in rows)


def test_shared_baseline_needs_no_target_and_obeys_identical_cutoff():
    candidate, context, candidates, outcomes = label_data()
    outcomes[0]["available_at"] = outcomes[-1]["available_at"]  # Too late despite earlier publication.
    labeled = ev.label_candidate(candidate, context, candidates, outcomes)
    history_only = [o for o in outcomes if o["candidate_id"] != candidate["candidate_id"]]
    baseline = ev.historical_baseline(
        candidate, context, candidates, history_only, source="manual_native_views"
    )
    for key in (
        "baseline_views",
        "baseline_sample_count",
        "baseline_observation_ids",
        "baseline_post_type_policy",
    ):
        assert baseline[key] == labeled[key]
    assert baseline["baseline_sample_count"] == 10 and "label" not in baseline and "views" not in baseline
    assert ev.label_candidate(candidate, context, candidates, history_only)["label"] is None


def forecast_fixture(store, monkeypatch, *, task="breakout_48h_v1", allow_missing=False):
    judgment = attach_data(store)
    configuration = {
        k: judgment[k]
        for k in (
            "profile_id",
            "audience_id",
            "audience_version",
            "rubric_version",
            "schema_version",
            "model_requested",
        )
    }
    configuration["rubric_hash"] = judgment["provenance"]["rubric_hash"]
    predictor = {
        "predictor_id": "isolated-inference-test",
        "approval": {"accepted": True},
        "configuration": configuration,
        "task": task,
        "model": {"kind": "constant", "prevalence": 0.2},
        "tier_boundaries": [0.01, 0.05, 0.15, 0.35],
        "comparison_population": "Isolated unit-test fixture; no predictive evidence",
        "source_policy": {
            "metric": "views",
            "source": "manual_native_views",
            "mapping": None,
            "allow_missing_baseline": allow_missing,
        },
    }
    store.put("predictor", predictor["predictor_id"], predictor)
    # Exercise inference independently of the separately tested evidence gates.
    monkeypatch.setattr(ev, "_predictor_lineage_errors", lambda *args: [])
    with store.connect() as db:
        db.execute("DELETE FROM records WHERE kind='outcome' AND id='target-observation'")
    return predictor


def test_forecast_uses_frozen_source_without_target_observation(tmp_path, monkeypatch):
    store = Store(tmp_path)
    forecast_fixture(store, monkeypatch)
    observation = deepcopy(store.list("outcome")[0])
    observation.update(
        observation_id="unrelated-source", source="unrelated_impression_analytics", metric="impressions"
    )
    store.put("outcome", observation["observation_id"], observation)
    observation.update(
        observation_id="other-views-source", metric="views", source="unrelated_views_provider", views=999999
    )
    store.put("outcome", observation["observation_id"], observation)
    before = deepcopy(store.list("outcome"))
    original_predict = ev.model_predict

    def inspect_features(model, rows):
        assert rows[0]["label"] is None and rows[0]["label_available_at"] is None
        assert "views" not in rows[0]["label_details"]
        assert rows[0]["features"]["log_baseline_views"] == math.log1p(1000)
        return original_predict(model, rows)

    monkeypatch.setattr(ev, "model_predict", inspect_features)
    result = ev.predict(store, "judgment")
    assert result["available"] and result["breakout_probability"] == 0.2
    assert result["historical_baseline"]["baseline_sample_count"] == 11
    assert result["source_policy"]["source"] == "manual_native_views"
    assert store.list("outcome") == before
    assert not any(o["candidate_id"] == "target" for o in before)


@pytest.mark.parametrize("allow_missing", [False, True])
def test_absolute_cold_start_requires_evaluated_missingness_support(tmp_path, monkeypatch, allow_missing):
    store = Store(tmp_path)
    forecast_fixture(store, monkeypatch, task="absolute_48h_v1", allow_missing=allow_missing)
    with store.connect() as db:
        db.execute("DELETE FROM records WHERE kind='outcome'")
    result = ev.predict(store, "judgment")
    assert result["available"] is allow_missing
    if allow_missing:
        assert result["historical_baseline"]["baseline_views"] is None
        assert result["historical_baseline"]["baseline_sample_count"] == 0
    else:
        assert "not represented" in result["reason"]
    assert not store.list("outcome")


def test_mixed_views_sources_require_explicit_cohort_filter(tmp_path):
    store = Store(tmp_path)
    first = attach_data(store)
    run = deepcopy(store.get("run", "judgment"))
    candidate = deepcopy(run["request"]["candidate"])
    candidate.update(candidate_id="second-target", author_id="second-author")
    run["request"]["candidate"] = candidate
    run["judgment_id"] = "second-judgment"
    judgment = dict(first, candidate_id="second-target", judgment_id="second-judgment")
    store.put("candidate", "second-target:1", candidate)
    store.put("judgment", "second-judgment", judgment)
    store.put("run", "second-judgment", run)
    outcome = deepcopy(store.get("outcome", "target-observation"))
    outcome.update(observation_id="second-outcome", candidate_id="second-target", source="other-native-views")
    store.put("outcome", "second-outcome", outcome)
    mixed = ev.eligibility(store, task="absolute_48h_v1")
    assert mixed["eligible_rows"] == 0
    assert mixed["exclusion_counts"]["mixed_views_sources_without_mapping_select_explicit_source"] == 2
    selected = ev.eligibility(store, task="absolute_48h_v1", cohort={"outcome_source": "manual_native_views"})
    assert selected["eligible_rows"] == 1
