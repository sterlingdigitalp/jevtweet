"""Outcome-independent retry selection, source isolation and forecast resolution.

These fixtures are private, temporary test data. Predictor-resolution tests
isolate artifact lineage, like the existing inference tests; originating
experiment, promotion and holdout checks remain covered in test_evaluation.py.
"""

from copy import deepcopy
from datetime import timedelta

import pytest
from test_evaluation import attach_data, forecast_fixture

from jevtweet import evaluation as ev
from jevtweet.storage import Store


def add_attempt(store, judgment_id, *, seconds=1, **changes):
    judgment = deepcopy(store.get("judgment", "judgment"))
    run = deepcopy(store.get("run", "judgment"))
    judgment.update(changes)
    judgment.update(
        judgment_id=judgment_id,
        created_at=(ev._date(judgment["created_at"]) + timedelta(seconds=seconds)).isoformat(),
    )
    run["judgment_id"] = judgment_id
    for field in ("execution_mode", "profile_id", "audience_id"):
        run["request"][field] = judgment[field]
    judgment["input_hash"] = ev.digest(
        {
            "state": run["state"],
            "questions": run["questions"],
            "rubric": ev.RUBRIC,
            "model": judgment["model_requested"],
            "sdk": judgment["sdk_version"],
            "profile": judgment["profile_id"],
            "mode": judgment["execution_mode"],
        }
    )
    store.put("judgment", judgment_id, judgment)
    store.put("run", judgment_id, run)
    return judgment


@pytest.mark.parametrize("earlier", ["mock", "failed", "bad_lineage", "incomplete"])
def test_earlier_nonqualifying_attempt_cannot_hide_live_retry(tmp_path, earlier):
    store = Store(tmp_path)
    first = attach_data(store)
    add_attempt(store, "later-live")
    if earlier == "mock":
        first["execution_mode"] = "mock"
    elif earlier == "failed":
        first.update(status="failed", score_continuous=None)
    elif earlier == "bad_lineage":
        first["input_hash"] = "invalid"
    else:
        first["factors"][ev.SEMANTIC_NAMES[0]]["score"] = None
    store.put("judgment", "judgment", first, replace=True)

    dataset = ev.build_dataset(store)

    assert [row["judgment_id"] for row in dataset["rows"]] == ["later-live"]
    attempts = {a["judgment_id"]: a for a in dataset["judgment_attempts"]}
    assert not attempts["judgment"]["selected"] and attempts["judgment"]["reason"]
    assert attempts["later-live"]["selected"]
    assert dataset["judgment_selection_policy"]


def test_retry_selection_ignores_scores_and_outcomes_but_keeps_attempt_coverage(tmp_path):
    store = Store(tmp_path)
    first = attach_data(store)
    first.update(status="failed", score_continuous=None)
    add_attempt(store, "first-valid", seconds=1, score_continuous=1.1)
    add_attempt(store, "later-favorable", seconds=2, score_continuous=4.9)
    store.put("judgment", "judgment", first, replace=True)

    dataset = ev.build_dataset(store)

    assert [row["judgment_id"] for row in dataset["rows"]] == ["first-valid"]
    assert dataset["rows"][0]["editorial"] == 1.1
    attempts = {a["judgment_id"]: a for a in dataset["judgment_attempts"]}
    assert set(attempts) == {"judgment", "first-valid", "later-favorable"}
    assert attempts["first-valid"]["selected"]
    assert not attempts["later-favorable"]["selected"]
    assert attempts["later-favorable"]["reason"]
    coverage = dataset["failure_coverage"]
    assert coverage["candidates_with_attempts"] == 1
    assert coverage["candidates_with_failed_attempts"] == 1
    assert coverage["total_attempts"] == 3 and coverage["failed_attempts"] == 1

    # Outcome changes do not choose a different retry, and neither does a lower
    # score on the later result. Eligibility remains positive/negative agnostic.
    outcome = store.get("outcome", "target-observation")
    outcome["views"] = 500
    store.put("outcome", outcome["observation_id"], outcome, replace=True)
    later = store.get("judgment", "later-favorable")
    later["score_continuous"] = 1
    store.put("judgment", "later-favorable", later, replace=True)
    changed = ev.build_dataset(store)
    assert changed["rows"][0]["judgment_id"] == "first-valid"
    assert changed["rows"][0]["label"] == 0
    assert changed["judgment_attempts"] == dataset["judgment_attempts"]


def test_explicit_judgment_cohort_retains_excluded_attempt_provenance(tmp_path):
    store = Store(tmp_path)
    attach_data(store)
    add_attempt(store, "other-audience", audience_id="general_readers")

    dataset = ev.build_dataset(store, cohort={"audience_id": "general_readers"})

    assert dataset["rows"][0]["judgment_id"] == "other-audience"
    attempts = {a["judgment_id"]: a for a in dataset["judgment_attempts"]}
    assert not attempts["judgment"]["selected"] and attempts["judgment"]["reason"]
    assert attempts["other-audience"]["selected"]


def test_all_failed_candidate_is_counted_without_a_fabricated_evaluation_row(tmp_path):
    store = Store(tmp_path)
    judgment = attach_data(store)
    judgment.update(status="failed", score_continuous=None)
    store.put("judgment", "judgment", judgment, replace=True)
    add_attempt(store, "retry-failed")

    dataset = ev.build_dataset(store)

    assert not dataset["rows"]
    assert len(dataset["judgment_attempts"]) == 2
    assert all(not attempt["selected"] for attempt in dataset["judgment_attempts"])
    assert dataset["failure_coverage"]["candidates_with_failed_attempts"] == 1
    assert dataset["failure_coverage"]["failed_attempts"] == 2
    assert dataset["failure_coverage"]["candidates_without_qualifying_judgment"] >= 1


def test_retry_order_compares_instants_and_uses_stable_id_only_for_ties(tmp_path):
    store = Store(tmp_path)
    attach_data(store)
    later = add_attempt(store, "a-later-offset", score_continuous=4.9)
    # Lexically this date sorts before June 1, but its instant is one hour later.
    later["created_at"] = "2025-05-31T20:00:00-05:00"
    store.put("judgment", later["judgment_id"], later, replace=True)
    assert ev.build_dataset(store)["rows"][0]["judgment_id"] == "judgment"

    tied = add_attempt(store, "a-tied", seconds=0, score_continuous=1.1)
    assert ev.build_dataset(store)["rows"][0]["judgment_id"] == tied["judgment_id"]


def test_postpublication_prediction_is_rejected_before_selecting_a_valid_retry(tmp_path):
    store = Store(tmp_path)
    attach_data(store)
    add_attempt(store, "valid-prospective-retry")
    run = store.get("run", "judgment")
    run["request"]["context"]["prediction_cutoff"] = (
        ev._date(run["request"]["candidate"]["published_at"]) + timedelta(hours=1)
    ).isoformat()
    store.put("run", "judgment", run, replace=True)

    dataset = ev.build_dataset(store)

    assert [row["judgment_id"] for row in dataset["rows"]] == ["valid-prospective-retry"]
    first = next(a for a in dataset["judgment_attempts"] if a["judgment_id"] == "judgment")
    assert not first["selected"] and first["reason"] == "prediction_after_publication"


def dual_source_data(store):
    attach_data(store)
    for original in store.list("outcome"):
        other = deepcopy(original)
        other.update(observation_id="second-" + original["observation_id"], source="second_native_views")
        other["views"] = 16000 if original["candidate_id"] == "target" else 2000
        store.put("outcome", other["observation_id"], other)


@pytest.mark.parametrize(
    "source,baseline,target,label",
    [("manual_native_views", 1000, 15000, 1), ("second_native_views", 2000, 16000, 0)],
)
def test_explicit_source_selects_target_and_reconstructs_only_matching_history(
    tmp_path, source, baseline, target, label
):
    store = Store(tmp_path)
    dual_source_data(store)

    dataset = ev.build_dataset(store, cohort={"outcome_source": source})

    assert len(dataset["rows"]) == 1
    details = dataset["rows"][0]["label_details"]
    assert details["source"] == source and details["views"] == target
    assert details["baseline_views"] == baseline and details["baseline_sample_count"] == 11
    assert details["label"] == label
    outcomes = {o["observation_id"]: o for o in store.list("outcome")}
    assert all(outcomes[oid]["source"] == source for oid in details["baseline_observation_ids"])


def test_two_valid_sources_without_explicit_selection_remain_ambiguous(tmp_path):
    store = Store(tmp_path)
    dual_source_data(store)
    dataset = ev.build_dataset(store)
    assert not dataset["rows"]
    assert dataset["exclusion_counts"]["ambiguous_metric_sources"] == 1


def test_explicit_source_never_substitutes_impressions_for_views(tmp_path):
    store = Store(tmp_path)
    dual_source_data(store)
    for outcome in store.list("outcome"):
        if outcome["source"] == "second_native_views":
            outcome["metric"] = "impressions"
            store.put("outcome", outcome["observation_id"], outcome, replace=True)

    selected = ev.build_dataset(store, cohort={"outcome_source": "second_native_views"})
    assert not selected["rows"]
    valid = ev.build_dataset(store, cohort={"outcome_source": "manual_native_views"})
    assert valid["rows"][0]["label_details"]["baseline_views"] == 1000


def test_matching_source_impressions_cannot_complete_historical_baseline(tmp_path):
    store = Store(tmp_path)
    attach_data(store)
    for observation_id in ("history-observation-0", "history-observation-1"):
        outcome = store.get("outcome", observation_id)
        outcome["metric"] = "impressions"
        store.put("outcome", observation_id, outcome, replace=True)

    dataset = ev.build_dataset(store, cohort={"outcome_source": "manual_native_views"})

    assert not dataset["rows"]
    assert dataset["exclusion_counts"]["insufficient_as_of_baseline"] == 1


def two_audience_predictors(store, monkeypatch):
    first = forecast_fixture(store, monkeypatch)
    add_attempt(store, "general-judgment", audience_id="general_readers")
    second = deepcopy(first)
    second["predictor_id"] = "general-predictor"
    second["configuration"]["audience_id"] = "general_readers"
    store.put("predictor", second["predictor_id"], second)
    return first, second


def test_approved_audience_predictors_coexist_without_global_newest_shadowing(tmp_path, monkeypatch):
    store = Store(tmp_path)
    first, second = two_audience_predictors(store, monkeypatch)

    original_audience = ev.predict(store, "judgment")
    general_audience = ev.predict(store, "general-judgment")

    assert original_audience["available"] and original_audience["predictor_id"] == first["predictor_id"]
    assert general_audience["available"] and general_audience["predictor_id"] == second["predictor_id"]


def test_explicit_predictor_selection_rejects_incompatible_audience(tmp_path, monkeypatch):
    store = Store(tmp_path)
    first, second = two_audience_predictors(store, monkeypatch)
    selected = ev.predict(store, "judgment", predictor_id=first["predictor_id"])
    assert selected["available"] and selected["predictor_id"] == first["predictor_id"]
    mismatch = ev.predict(store, "judgment", predictor_id=second["predictor_id"])
    assert not mismatch["available"] and mismatch["breakout_probability"] is None


def test_newer_invalid_lineage_does_not_shadow_compatible_approved_predictor(tmp_path, monkeypatch):
    store = Store(tmp_path)
    first = forecast_fixture(store, monkeypatch)
    invalid = deepcopy(first)
    invalid["predictor_id"] = "invalid-newer-artifact"
    store.put("predictor", invalid["predictor_id"], invalid)
    monkeypatch.setattr(
        ev,
        "_predictor_lineage_errors",
        lambda _store, p: (
            ["Invalid originating evaluation"] if p["predictor_id"] == invalid["predictor_id"] else []
        ),
    )

    result = ev.predict(store, "judgment")

    assert result["available"] and result["predictor_id"] == first["predictor_id"]


def test_multiple_compatible_predictors_require_explicit_selection(tmp_path, monkeypatch):
    store = Store(tmp_path)
    first = forecast_fixture(store, monkeypatch)
    second = deepcopy(first)
    second["predictor_id"] = "another-approved-predictor"
    store.put("predictor", second["predictor_id"], second)

    ambiguous = ev.predict(store, "judgment")

    assert not ambiguous["available"]
    assert ambiguous["breakout_probability"] is None
    assert "select" in ambiguous["reason"].lower()
    assert ev.predict(store, "judgment", predictor_id=first["predictor_id"])["available"]
    assert ev.predict(store, "judgment", predictor_id=second["predictor_id"])["available"]


def test_task_selection_resolves_compatible_predictors_without_cross_task_default(tmp_path, monkeypatch):
    store = Store(tmp_path)
    breakout = forecast_fixture(store, monkeypatch)
    absolute = deepcopy(breakout)
    absolute.update(predictor_id="absolute-approved-predictor", task="absolute_48h_v1")
    store.put("predictor", absolute["predictor_id"], absolute)

    assert not ev.predict(store, "judgment")["available"]
    breakout_result = ev.predict(store, "judgment", task="breakout_48h_v1")
    absolute_result = ev.predict(store, "judgment", task="absolute_48h_v1")
    assert breakout_result["available"] and breakout_result["predictor_id"] == breakout["predictor_id"]
    assert absolute_result["available"] and absolute_result["predictor_id"] == absolute["predictor_id"]
    mismatch = ev.predict(store, "judgment", task="breakout_48h_v1", predictor_id=absolute["predictor_id"])
    assert not mismatch["available"]
