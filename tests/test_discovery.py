import asyncio
from copy import deepcopy

import numpy as np
import pytest

from jevtweet.discovery import development_errors, discover, validate_proposals
from jevtweet.evaluation import METADATA_NAMES, SEMANTIC_NAMES, _synthetic_rows
from jevtweet.storage import Store


def proposal(name="demonstrated_result"):
    return {
        "feature_id": name,
        "version": "v1",
        "type": "noul",
        "instructions": "Does candidate.text supply a concrete demonstration?",
        "reviewed_by": "synthetic-human-review",
        "reviewed_at": "2025-01-01T00:00:00Z",
        "hypothesis": "A supplied concrete demonstration may add incremental value.",
    }


@pytest.fixture
def research(tmp_path):
    store = Store(tmp_path)
    rows = _synthetic_rows(80)
    rng = np.random.default_rng(42)
    for i, row in enumerate(rows):
        row["label"] = i % 2
        row["text"] = f"Original synthetic {'demonstration' if i % 2 else 'generic'} wording {i}"
        row["features"] = {name: float(rng.normal()) for name in SEMANTIC_NAMES + METADATA_NAMES}
    partitions = {"train": rows[:40], "selection": rows[40:60], "calibration": rows[60:70]}
    experiment = {
        "experiment_id": "experiment",
        "synthetic": True,
        "development_partitions": partitions,
        "split_manifest": {
            "partitions": {
                **{k: [r["key"] for r in v] for k, v in partitions.items()},
                "test": [r["key"] for r in rows[70:]],
            }
        },
        "development_errors": [
            {
                "key": r["key"],
                "label": r["label"],
                "probability": 0.5,
                "absolute_error": 0.5,
                "partition": "selection",
            }
            for r in rows[40:60]
        ],
        "failure_examples": [{"key": rows[70]["key"], "secret_test_label": rows[70]["label"]}],
        "metrics": {"private_test_metric": 1},
    }
    store.put("experiment", "experiment", experiment)
    return store, experiment


def test_development_view_never_exposes_test(research):
    store, experiment = research
    view = development_errors(store, "experiment")
    assert view["partition"] == "selection" and len(view["errors"]) == 20
    assert "failure_examples" not in view and "metrics" not in view
    test_keys = set(experiment["split_manifest"]["partitions"]["test"])
    assert not test_keys & {r["key"] for r in view["errors"]}


def test_incremental_feature_accepted_without_production_promotion(research):
    store, experiment = research
    calls = []

    async def provider(state, question):
        calls.append(deepcopy(state))
        assert "label" not in state and "features" not in state and "metrics" not in state
        return {
            "status": "ok",
            "execution_mode": "mock",
            "estimated_cost_usd": 0.00001,
            "value": float("demonstration" in state["candidate"]["text"]),
        }

    result = asyncio.run(
        discover(
            store,
            "experiment",
            [proposal()],
            feature_provider=provider,
            cost_limit_usd=0.1,
            cost_estimate_per_request_usd=0.0001,
            max_rows=60,
            max_requests=100,
        )
    )
    assert result["status"] == "complete" and result["features"][0]["accepted"]
    assert result["features"][0]["log_loss_improvement"] > 0.1
    assert len(calls) == 58 and result["request_count"] == 58
    assert not result["production_promoted"] and not result["test_exposed"] and not store.list("predictor")
    assert not set(result["development_keys"]) & set(experiment["split_manifest"]["partitions"]["test"])


def test_redundancy_and_failed_answers_reject(research):
    store, _ = research

    async def provider(state, question):
        return {"status": "ok", "execution_mode": "mock", "estimated_cost_usd": 0, "value": 0.5}

    result = asyncio.run(
        discover(
            store,
            "experiment",
            [proposal()],
            feature_provider=provider,
            cost_limit_usd=0.1,
            cost_estimate_per_request_usd=0.0001,
            max_rows=60,
        )
    )
    assert not result["features"][0]["accepted"]
    assert "redundant_or_constant_feature" in result["features"][0]["decision_reasons"]

    async def failed(state, question):
        return {"status": "failed", "execution_mode": "mock", "estimated_cost_usd": 0, "value": None}

    failed_result = asyncio.run(
        discover(
            store,
            "experiment",
            [proposal("another_result")],
            feature_provider=failed,
            cost_limit_usd=0.1,
            cost_estimate_per_request_usd=0.0001,
            max_rows=60,
        )
    )
    assert failed_result["features"][0]["coverage"] == 0
    assert not failed_result["features"][0]["accepted"]


def test_limits_review_and_contamination_fail_before_provider_call(research):
    store, experiment = research
    calls = []

    async def provider(*args):
        calls.append(1)

    kwargs = dict(
        feature_provider=provider, cost_limit_usd=0.001, cost_estimate_per_request_usd=0.0001, max_rows=60
    )
    with pytest.raises(ValueError, match="ceiling"):
        asyncio.run(discover(store, "experiment", [proposal()], **kwargs))
    assert not calls
    with pytest.raises(ValueError, match="one to"):
        validate_proposals([proposal(f"feature_{i}") for i in range(9)])
    bad = proposal()
    bad["instructions"] = "Read the viral label from the test set."
    with pytest.raises(ValueError, match="forbidden"):
        validate_proposals([bad])
    experiment["development_partitions"]["train"][0]["key"] = experiment["split_manifest"]["partitions"][
        "test"
    ][0]
    store.put("experiment", "experiment", experiment, replace=True)
    kwargs["cost_limit_usd"] = 0.1
    with pytest.raises(ValueError, match="contamination"):
        asyncio.run(discover(store, "experiment", [proposal()], **kwargs))
    assert not calls


def test_iteration_and_active_caps(research):
    store, _ = research
    for i in range(5):
        store.put(
            "discovery",
            str(i),
            {
                "discovery_id": str(i),
                "experiment_id": "experiment",
                "cost_limit_usd": 0.1,
                "charged_or_reserved_usd": 0,
                "features": [],
            },
        )
    with pytest.raises(ValueError, match="five-iteration"):
        asyncio.run(
            discover(
                store,
                "experiment",
                [proposal()],
                feature_provider=lambda *a: {},
                cost_limit_usd=0.1,
                cost_estimate_per_request_usd=0.0001,
                max_rows=60,
            )
        )
