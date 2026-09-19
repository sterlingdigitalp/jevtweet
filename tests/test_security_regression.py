"""Local HTTP and prediction-time security tests using original synthetic fixtures."""
from datetime import datetime, timedelta, timezone
import importlib

import pytest
from fastapi.testclient import TestClient

from jevtweet.contracts import Candidate, EvidenceText, HistoricalMetadata, PredictionContext, Reference, canonical
from jevtweet.service import Service
from jevtweet.settings import Settings
from jevtweet.state import audiences, build_state, select_references
from jevtweet.storage import Store

T = datetime(2025, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Importing the ASGI module creates its default app. Keep even that store private in tmp.
    monkeypatch.setenv("JEVTWEET_DATA_DIR", str(tmp_path / "default-data"))
    monkeypatch.setenv("JEVTWEET_ACCOUNT_DIR", str(tmp_path / "default-account"))
    api = importlib.import_module("jevtweet.api")
    service = Service(Settings(data_dir=tmp_path / "data", account_dir=tmp_path / "account"))
    with TestClient(api.create_app(service=service), base_url="http://localhost:8000", raise_server_exceptions=False) as test_client:
        yield test_client


def test_local_mutations_require_session_token_and_allowed_origin(client):
    response = client.get("/api/session")
    assert response.status_code == 200
    token = response.json()["csrf_token"]
    body = {"content": '{"candidate_id":"csrf-fixture","text":"A synthetic CSRF fixture","synthetic":true}', "format": "jsonl"}
    assert client.post("/api/import", json=body).status_code == 403
    assert client.post("/api/import", json=body, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    for origin in ("https://evil.example", "http://localhost.evil.example:8000", "null", "http://127.0.0.1:9090"):
        assert client.post("/api/import", json=body, headers={"X-CSRF-Token": token, "Origin": origin}).status_code == 403
    assert client.post("/api/import", json=body, headers={"X-CSRF-Token": token, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/api/session", headers={"Host": "rebinding.evil.example"}).status_code == 400
    accepted = client.post("/api/import", json=body, headers={"X-CSRF-Token": token, "Origin": "http://localhost:8000"})
    assert accepted.status_code == 200 and accepted.json()["accepted"] == 1
    assert accepted.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in accepted.headers["content-security-policy"]


def test_malformed_origin_or_length_is_client_error_not_server_error(client):
    for headers in ({"Origin": "http://localhost:invalid"}, {"Origin": "http://[broken"}, {"Content-Length": "NaN"}, {"Content-Length": "-1"}):
        response = client.get("/api/session", headers=headers)
        assert 400 <= response.status_code < 500


def test_occurrence_and_availability_cutoffs_are_both_enforced():
    candidate = Candidate(candidate_id="target", text="A concrete repair for database deployment", content_available_at=T, provenance="hidden-winner-label.csv")
    context = PredictionContext(
        prediction_cutoff=T,
        topic=EvidenceText(text="FUTURE_EVENT", occurred_at=T + timedelta(seconds=1), available_at=T),
        historical=HistoricalMetadata(followers=987654, observed_at=T + timedelta(seconds=1), available_at=T, provenance="hidden-label"),
        references=[Reference(candidate_id="published-future", text="FUTURE_REFERENCE", published_at=T + timedelta(seconds=1), available_at=T)],
    )
    state = build_state(candidate, context, audiences()[0])
    encoded = canonical(state)
    assert state["topic"] is None and state["historical"] is None and not state["references"]
    assert all(secret not in encoded for secret in ("FUTURE_EVENT", "FUTURE_REFERENCE", "987654", "hidden-label", "hidden-winner-label"))


def test_ever_heldout_candidate_is_not_retrieved_after_later_split_reassignment(tmp_path):
    store = Store(tmp_path)
    candidate = Candidate(candidate_id="target", text="database deployment migration test reliability", content_available_at=T)
    reference = Candidate(candidate_id="reference", text="database deployment safeguards catch migration errors before release", published_at=T - timedelta(days=4), content_available_at=T - timedelta(days=4))
    store.put("candidate", "reference:1", reference)
    store.put("experiment", "first", {"split_manifest": {"partitions": {"test": ["reference:1"]}}})
    store.put("experiment", "second", {"split_manifest": {"partitions": {"train": ["reference:1"]}}})
    assert not select_references(store, candidate, PredictionContext(prediction_cutoff=T))
