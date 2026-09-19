"""Offline regressions for explicit evaluation stages and request declarations."""

import pytest
from fastapi.testclient import TestClient

from jevtweet.api import create_app
from jevtweet.service import Service
from jevtweet.settings import Settings


@pytest.fixture
def local_client(tmp_path):
    service = Service(Settings(data_dir=tmp_path / "data", account_dir=tmp_path / "account"))
    with TestClient(create_app(service=service), base_url="http://127.0.0.1:8000") as client:
        client.headers["X-CSRF-Token"] = client.get("/api/session").json()["csrf_token"]
        yield client, service.store


def test_preflight_missing_declarations_does_not_consume_or_freeze(local_client):
    client, store = local_client
    response = client.post("/api/experiments/preflight", json={"synthetic": False})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "not_ready"
    assert store.list("holdout") == []
    assert store.list("experiment") == []


def test_readiness_is_available_without_opening_test(local_client):
    client, store = local_client
    response = client.post("/api/experiments/readiness", json={"synthetic": False})
    assert response.status_code == 200, response.text
    assert response.json()["test_outcomes_inspected"] is False
    assert response.json()["predictive_validation"] == "not_established"
    assert store.list("holdout") == []


def test_opening_requires_exact_hash_and_cannot_rewrite_declarations(local_client):
    client, _ = local_client
    response = client.post(
        "/api/experiments/missing/open-holdout",
        json={"frozen_candidate_hash": "a" * 64, "comparison_population": "rewritten"},
    )
    assert response.status_code == 422, response.text


def test_evaluation_cap_is_typed_at_request_boundary(local_client):
    client, _ = local_client
    for maximum in (39, 5001):
        response = client.post("/api/experiments/preflight", json={"max_rows": maximum})
        assert response.status_code == 422, response.text


def test_api_preflight_freeze_open_and_refuse_declaration_rewrite(local_client):
    client, store = local_client
    body = {
        "synthetic": True,
        "task": "absolute_48h_v1",
        "max_rows": 160,
        "sampling_declaration": "Original synthetic workflow fixture; no predictive evidence",
        "comparison_population": "Synthetic software verification only",
    }
    preflight = client.post("/api/experiments/preflight", json=body)
    assert preflight.status_code == 200, preflight.text
    assert preflight.json()["ready_to_freeze"] is True
    assert store.list("holdout") == [] and store.list("experiment") == []
    frozen = client.post("/api/experiments/freeze", json=body)
    assert frozen.status_code == 200, frozen.text
    frozen = frozen.json()
    assert frozen["status"] == "frozen"
    assert not frozen["metrics"]
    assert store.list("holdout") == []
    endpoint = f"/api/experiments/{frozen['experiment_id']}/open-holdout"
    opening = {"frozen_candidate_hash": frozen["frozen_candidate_hash"]}
    rewrite = client.post(endpoint, json=dict(opening, representative_sampling=True))
    assert rewrite.status_code == 422
    wrong_hash = client.post(endpoint, json={"frozen_candidate_hash": "0" * 64})
    assert wrong_hash.status_code == 400
    assert store.list("holdout") == []
    evaluated = client.post(endpoint, json=opening)
    assert evaluated.status_code == 200, evaluated.text
    evaluated = evaluated.json()
    assert evaluated["status"] == "evaluated"
    assert evaluated["synthetic"] is True and not evaluated["promotion"]["eligible"]
    assert evaluated["comparison_population"] == body["comparison_population"]
    assert evaluated["sampling_declaration"] == body["sampling_declaration"]
    assert store.list("predictor") == []
