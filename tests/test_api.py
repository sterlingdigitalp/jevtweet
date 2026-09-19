import json
from pathlib import Path

from fastapi.testclient import TestClient

from jevtweet.api import create_app
from jevtweet.service import Service
from jevtweet.settings import Settings


def test_api_paste_inspect_compare_import_outcome_and_export(tmp_path):
    service = Service(Settings(data_dir=tmp_path / "data", account_dir=tmp_path / "account"))
    with TestClient(create_app(service=service), base_url="http://127.0.0.1:8000") as client:
        session = client.get("/api/session").json()
        headers = {"X-CSRF-Token": session["csrf_token"]}
        body = {
            "candidate": {
                "candidate_id": "api-one",
                "text": "A concrete synthetic migration test",
                "content_available_at": "2026-01-01T00:00:00Z",
                "synthetic": True,
            },
            "context": {"prediction_cutoff": "2026-01-01T00:00:00Z"},
        }
        r = client.post("/api/judge", json=body, headers=headers)
        assert r.status_code == 200, r.text
        result = r.json()
        assert (
            result["status"] == "scored"
            and result["execution_mode"] == "mock"
            and result["breakout_probability"] is None
        )
        inspected = client.get("/api/judgments/" + result["judgment_id"]).json()
        assert inspected["state"]["candidate"]["text"] == body["candidate"]["text"] and inspected["questions"]
        second = json.loads(json.dumps(body))
        second["candidate"].update(
            candidate_id="api-two", text="We replay every synthetic migration before merge."
        )
        comparison = client.post("/api/compare", json={"requests": [body, second]}, headers=headers).json()
        assert comparison["comparable"] and len(comparison["results"]) == 2
        fixture = Path("fixtures/candidates_synthetic.jsonl").read_text()
        preview = client.post(
            "/api/import", json={"content": fixture, "preview": True}, headers=headers
        ).json()
        assert preview["accepted"] == 4 and len(client.get("/api/candidates").json()) == 2
        assert client.post("/api/import", json={"content": fixture}, headers=headers).json()["accepted"] == 4
        outcome = Path("fixtures/outcomes_synthetic.jsonl").read_text()
        assert (
            client.post("/api/outcomes/import", json={"content": outcome}, headers=headers).json()["accepted"]
            == 2
        )
        export = client.get("/api/export?format=csv&execution_mode=mock")
        assert export.status_code == 200 and "score_continuous" in export.text
        evaluation = client.post("/api/experiments", json={"synthetic": False}, headers=headers).json()
        assert evaluation["status"] == "not_ready" and not evaluation["forecast_available"]
        assert client.post("/api/discovery", json={}, headers=headers).status_code == 422
        assert client.post("/api/jobs", json={"candidate_ids": []}, headers=headers).status_code == 422
