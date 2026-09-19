from datetime import datetime, timedelta, timezone

import pytest

from jevtweet.prospective import append_prediction, collection_due, verify_log, verify_observation_window

T = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)


def _entry(pid="p1"):
    return {
        "method": "B",
        "target": {"post_id": pid, "claimed_published_at": T.isoformat()},
        "evidence_ids": ["e1"],
        "estimate": 1.5,
    }


def test_append_verify_and_write_once(tmp_path):
    log = tmp_path / "log.jsonl"
    first = append_prediction(log, _entry())
    assert first["prediction_id"] == "pred-00001" and first["prev_hash"] == "genesis"
    append_prediction(log, _entry("p2"))
    assert verify_log(log) == {"valid": True, "entries": 2}
    with pytest.raises(ValueError, match="rite-once"):
        append_prediction(log, _entry())


def test_missing_field_rejected(tmp_path):
    entry = _entry()
    del entry["estimate"]
    with pytest.raises(ValueError, match="estimate"):
        append_prediction(tmp_path / "log.jsonl", entry)


def test_collection_due_and_window_tolerance(tmp_path):
    log = tmp_path / "log.jsonl"
    append_prediction(log, _entry())
    assert collection_due(log, T + timedelta(hours=47)) == []
    assert len(collection_due(log, T + timedelta(hours=48))) == 1
    assert verify_observation_window(T, T + timedelta(hours=48, minutes=30))
    assert not verify_observation_window(T, T + timedelta(hours=50))
