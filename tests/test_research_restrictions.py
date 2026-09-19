"""Invented fixtures only: permanent diagnostic membership is not an editable flag."""

import asyncio
from copy import deepcopy

import pytest
from test_evaluation import attach_data, forecast_fixture

from jevtweet import evaluation as ev
from jevtweet.storage import Store


@pytest.fixture
def stores(tmp_path):
    registry = Store(tmp_path / "shared-account")
    first, second = Store(tmp_path / "first"), Store(tmp_path / "second")
    for store in (first, second):
        store.restriction_registry_dir = registry.data_dir
    return first, second, registry


def register(store, candidates, registry):
    from jevtweet.research_restrictions import register_diagnostic_only

    return register_diagnostic_only(store, candidates, "invented-unit-test-source", registry=registry)


@pytest.mark.parametrize("mutation", ["rekey", "near_duplicate", "changed_flags", "thread"])
def test_registry_survives_reimport_and_identity_changes(stores, mutation):
    from jevtweet.research_restrictions import restricted_candidates

    first, second, registry = stores
    original = {
        "candidate_id": "invented-a",
        "candidate_version": 1,
        "text": "The paper crane workshop starts beside the quiet botanical garden tomorrow morning",
        "thread_id": "invented-thread",
        "synthetic": False,
    }
    register(first, [original], registry)
    changed = dict(original, candidate_id="invented-new-id", candidate_version=99, thread_id=None)
    if mutation == "near_duplicate":
        changed["text"] += " now"
    elif mutation == "changed_flags":
        changed.update(synthetic=True, provenance="representative", published_at="2030-01-01T00:00:00Z")
    elif mutation == "thread":
        changed.update(text="Unrelated words in the same conversation", thread_id=original["thread_id"])
    found = restricted_candidates(second, [changed])
    assert found[ev._key(changed)]["reason"] == "permanent_diagnostic_only_corpus"
    assert first.list("diagnostic_restriction") == registry.list("diagnostic_restriction")


def test_registration_is_idempotent_and_local_copy_survives_missing_registry(stores, tmp_path):
    from jevtweet.research_restrictions import restricted_candidates

    first, _, registry = stores
    candidate = {"candidate_id": "invented-local", "candidate_version": 1, "text": "Lantern repair"}
    assert register(first, [candidate], registry) == register(first, [candidate], registry)
    assert len(registry.list("diagnostic_restriction")) == 1
    first.restriction_registry_dir = tmp_path / "unavailable-other-registry"
    assert ev._key(candidate) in restricted_candidates(first, [candidate])


def test_restriction_excludes_before_any_target_label_and_keeps_attempt(stores, monkeypatch):
    first, _, registry = stores
    judgment = attach_data(first)
    candidate = first.get("candidate", "target:1")
    register(first, [candidate], registry)
    monkeypatch.setattr(ev, "label_candidate", lambda *a, **k: pytest.fail("Restricted label was read"))
    result = ev.build_dataset(first)
    assert result["rows"] == [] and result["labels"] == []
    assert result["exclusion_counts"]["permanent_diagnostic_only_corpus"]
    attempt = next(a for a in result["judgment_attempts"] if a["judgment_id"] == "judgment")
    assert not attempt["selected"] and attempt["reason"] == "permanent_diagnostic_only_corpus"
    # Registration never rewrites an editorial judgment or its score.
    assert first.get("judgment", "judgment") == judgment


def test_diagnostic_rows_never_enter_development_or_protected_test_membership(stores):
    first, _, registry = stores
    attach_data(first)
    register(first, first.list("candidate"), registry)
    data = ev.prepare_development(first)
    assert not data["rows"] and not data["test_entries"] and not data["protected_test_keys"]
    assert not first.list("development_exposure")
    assert not first.list("holdout")


def test_restricted_history_is_removed_before_reconstructing_baseline(stores):
    first, _, registry = stores
    attach_data(first)
    restricted_history = []
    for index in (0, 1):
        candidate = first.get("candidate", f"history-{index}:1")
        candidate["text"] = f"Invented archival ceramics anecdote {index}"
        first.put("candidate", ev._key(candidate), candidate, replace=True)
        restricted_history.append(candidate)
    register(first, restricted_history, registry)
    result = ev.build_dataset(first)
    assert not result["rows"]
    target = next(label for label in result["labels"] if label["key"] == "target:1")
    assert target["reason"] == "insufficient_as_of_baseline"
    assert target["baseline_sample_count"] == 9
    assert not any(
        observation in target["baseline_observation_ids"]
        for observation in ("history-observation-0", "history-observation-1")
    )


def test_frozen_before_registration_cannot_open_holdout(stores):
    first, _, registry = stores
    frozen = ev.freeze_evaluation(first, synthetic=True)
    assert frozen["status"] == "frozen"
    register(first, [frozen["test_input_rows"][0]], registry)
    with pytest.raises(ValueError, match="diagnostic"):
        ev.open_holdout(first, frozen["experiment_id"], frozen_candidate_hash=frozen["frozen_candidate_hash"])
    assert not first.list("holdout")
    assert not first.get("experiment", frozen["experiment_id"])["final_test_opened"]


def test_new_restriction_revokes_promotion_even_if_other_gates_approve(stores, monkeypatch):
    first, _, registry = stores
    row = ev._synthetic_rows(1)[0]
    report = {
        "experiment_id": "invented-experiment",
        "status": "evaluated",
        "audit": [],
        "development_rows": [row],
    }
    first.put("experiment", report["experiment_id"], report)
    register(first, [row], registry)
    monkeypatch.setattr(ev, "_promotion_gates", lambda *a: {"eligible": True, "reasons": []})
    result = ev.promote(first, report["experiment_id"], approved_by="unit-test", rationale="Exercise guard")
    assert not result["forecast_available"]
    assert "permanent_diagnostic_only_corpus" in result["promotion"]["reasons"]
    assert not first.list("predictor")


def test_new_restriction_invalidates_approved_predictor_lineage(stores):
    first, _, registry = stores
    row = ev._synthetic_rows(1)[0]
    first.put(
        "experiment",
        "prior-approved",
        {"status": "evaluated", "promotion": {"approved": True}, "development_rows": [row]},
    )
    register(first, [row], registry)
    errors = ev._predictor_lineage_errors(first, {"predictor_id": "prior-approved"})
    assert errors and "diagnostic" in errors[0]


def test_restricted_candidate_cannot_get_forecast_from_unrelated_valid_predictor(stores, monkeypatch):
    first, _, registry = stores
    forecast_fixture(first, monkeypatch)
    candidate = deepcopy(first.get("candidate", "target:1"))
    register(first, [candidate], registry)
    result = ev.predict(first, "judgment")
    assert not result["available"] and "diagnostic" in result["reason"]


@pytest.mark.parametrize("action", ["development_errors", "discover"])
def test_saved_experiment_cannot_feed_discovery_after_registration(stores, monkeypatch, action):
    from jevtweet import discovery

    first, _, registry = stores
    rows = ev._synthetic_rows(80)
    for index, row in enumerate(rows):
        row["label"] = index % 2
    partitions = {"train": rows[:40], "selection": rows[40:60], "calibration": rows[60:70]}
    report = {
        "experiment_id": "invented-prior-research",
        "synthetic": True,
        "development_partitions": partitions,
        "development_errors": [],
        "split_manifest": {
            "partitions": {
                **{name: [row["key"] for row in group] for name, group in partitions.items()},
                "test": [row["key"] for row in rows[70:]],
            }
        },
    }
    first.put("experiment", report["experiment_id"], report)
    register(first, [rows[0]], registry)
    calls = []

    async def provider(*args):
        calls.append(args)
        pytest.fail("Restricted discovery reached provider")

    monkeypatch.setattr(discovery, "fit_model", lambda *args: pytest.fail("Restricted research fitted model"))
    with pytest.raises(ValueError, match="diagnostic"):
        if action == "development_errors":
            discovery.development_errors(first, report["experiment_id"])
        else:
            asyncio.run(
                discovery.discover(
                    first,
                    report["experiment_id"],
                    [
                        {
                            "feature_id": "invented_concrete_detail",
                            "version": "v1",
                            "type": "noul",
                            "instructions": "Does candidate.text contain a concrete detail?",
                            "reviewed_by": "unit-test-review",
                            "reviewed_at": "2025-01-01T00:00:00Z",
                            "hypothesis": "Invented test feature may add detail.",
                        }
                    ],
                    feature_provider=provider,
                    cost_limit_usd=0.1,
                    cost_estimate_per_request_usd=0.0001,
                    max_rows=60,
                )
            )
    assert not calls and not first.list("discovery")


def test_reattached_windows_in_another_store_do_not_release_research_restriction(stores, monkeypatch):
    first, second, registry = stores
    original = {
        "candidate_id": "original-import",
        "candidate_version": 1,
        "text": "A concrete result with evidence",
    }
    register(first, [original], registry)
    attach_data(second)  # Invents complete 48-hour windows and author history after registration.
    monkeypatch.setattr(ev, "label_candidate", lambda *a, **k: pytest.fail("Restricted label was read"))
    data = ev.build_dataset(second)
    assert not data["rows"] and not data["labels"]
    assert data["exclusion_counts"]["permanent_diagnostic_only_corpus"]
    frozen = ev.freeze_evaluation(second)
    assert frozen["status"] == "not_ready"
    assert not second.list("evaluation_freeze") and not second.list("holdout")
    assert len(second.list("outcome")) == 12  # Preserve supplied measurements; deny predictive use.


@pytest.mark.parametrize("copy_report_only", [False, True])
def test_saved_report_retains_baseline_identity_restrictions(stores, copy_report_only):
    from jevtweet.research_restrictions import report_restrictions

    first, second, registry = stores
    attach_data(first)
    history = first.get("candidate", "history-0:1")
    history["text"] = "Invented orchard notebooks contain seasonal pear harvest observations"
    first.put("candidate", ev._key(history), history, replace=True)
    data = ev.build_dataset(first)
    assert len(data["rows"]) == 1
    report = {"development_rows": data["rows"]}
    register(first, [history], registry)
    checked_store = second if copy_report_only else first
    found = report_restrictions(checked_store, report)
    assert "history-0:1" in found


def test_legacy_report_resolves_restricted_baseline_without_loading_outcome_body(stores, monkeypatch):
    from jevtweet.research_restrictions import report_restrictions

    first, _, registry = stores
    attach_data(first)
    history = first.get("candidate", "history-0:1")
    history["text"] = "Invented orchard notebooks contain seasonal pear harvest observations"
    first.put("candidate", ev._key(history), history, replace=True)
    rows = ev.build_dataset(first)["rows"]
    rows[0]["label_details"].pop("baseline_identities")
    register(first, [history], registry)
    original_get = first.get

    def guarded_get(kind, identity):
        assert kind != "outcome", "Restriction checks must not load historical outcome values"
        return original_get(kind, identity)

    monkeypatch.setattr(first, "get", guarded_get)
    assert "history-0:1" in report_restrictions(first, {"development_rows": rows})


def test_diagnostic_reference_cannot_feed_predictive_training_or_forecast(stores, monkeypatch):
    first, _, registry = stores
    forecast_fixture(first, monkeypatch)
    reference = {
        "candidate_id": "invented-reference",
        "candidate_version": 1,
        "text": "The ceramics kiln handbook describes clay mineral temperatures",
        "published_at": "2025-01-01T00:00:00Z",
        "available_at": "2025-01-01T00:00:00Z",
        "split": "development",
    }
    run = first.get("run", "judgment")
    run["request"]["context"]["references"] = [reference]
    first.put("run", "judgment", run, replace=True)
    assert len(ev._select_judgments(first, synthetic=False)["selected"]) == 1
    register(first, [reference], registry)
    result = ev.build_dataset(first)
    assert not result["rows"]
    assert result["exclusion_counts"]["permanent_diagnostic_only_reference"] == 1
    forecast = ev.predict(first, "judgment")
    assert not forecast["available"] and "diagnostic" in forecast["reason"]
