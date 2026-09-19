from datetime import datetime, timedelta, timezone

import pytest

from jevtweet.contracts import (
    Candidate,
    EvidenceText,
    HistoricalMetadata,
    OutcomeObservation,
    PredictionContext,
    Reference,
    canonical,
)
from jevtweet.corpus import attach_outcome, export_results, import_candidates
from jevtweet.state import audiences, build_state
from jevtweet.storage import Store

T = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_allowlist_future_evidence_and_reference_leakage():
    c = Candidate(
        candidate_id="target",
        text="A concrete repair for a broken migration",
        content_available_at=T,
        provenance="winner-100000-views.csv",
        post_type="reply",
        parent=EvidenceText(text="FUTURE_PARENT", occurred_at=T, available_at=T + timedelta(days=1)),
    )
    ctx = PredictionContext(
        prediction_cutoff=T,
        historical=HistoricalMetadata(
            followers=999999,
            observed_at=T + timedelta(days=1),
            available_at=T + timedelta(days=1),
            provenance="SECRET_LABEL",
        ),
        references=[
            Reference(
                candidate_id="ref",
                text="FUTURE_REFERENCE",
                published_at=T - timedelta(days=3),
                available_at=T + timedelta(days=1),
            ),
            Reference(
                candidate_id="test",
                text="HELDOUT",
                published_at=T - timedelta(days=3),
                available_at=T - timedelta(days=2),
                split="test",
            ),
        ],
    )
    state = build_state(c, ctx, audiences()[0])
    encoded = canonical(state)
    for forbidden in [
        "FUTURE_PARENT",
        "FUTURE_REFERENCE",
        "HELDOUT",
        "999999",
        "SECRET_LABEL",
        "winner-100000",
    ]:
        assert forbidden not in encoded
    assert "essential_parent_context" in state["evidence"]["missing"]
    assert state["references"] == []


def test_historical_cutoff_requires_snapshot():
    with pytest.raises(ValueError):
        build_state(
            Candidate(text="x", content_available_at=T + timedelta(seconds=1)),
            PredictionContext(prediction_cutoff=T),
            audiences()[0],
        )


def test_import_mapping_raw_rows_and_conflict(tmp_path):
    s = Store(tmp_path)
    content = "external_id,body,viral_label\nx,Use a tested migration,WINNER\n"
    r = import_candidates(s, content, "csv", {"candidate_id": "external_id", "text": "body"})
    assert r["accepted"] == 1 and r["unknown_fields_excluded"] == ["viral_label"]
    assert s.list("import")[0]["original_rows"][0]["viral_label"] == "WINNER"
    c = s.get("candidate", "x:1")
    assert "viral_label" not in c
    conflict = import_candidates(s, '{"candidate_id":"x","text":"changed"}', "jsonl")
    assert conflict["accepted"] == 0 and conflict["errors"]
    assert import_candidates(s, "not json", "jsonl")["errors"]


def test_outcomes_missing_not_zero_and_conflicts(tmp_path):
    s = Store(tmp_path)
    c = Candidate(candidate_id="x", text="Synthetic", published_at=T, content_available_at=T, synthetic=True)
    s.put("candidate", "x:1", c)
    o = OutcomeObservation(
        candidate_id="x",
        source="manual",
        observed_at=T + timedelta(hours=48),
        available_at=T + timedelta(hours=48),
        elapsed_hours=48,
        synthetic=True,
    )
    saved = attach_outcome(s, o)
    assert saved["views"] is None
    with pytest.raises(ValueError):
        attach_outcome(s, o.model_copy(update={"elapsed_hours": 168}))
    with pytest.raises(ValueError):
        attach_outcome(s, o.model_copy(update={"observation_id": "new", "views": 0}))


def test_csv_formula_escaping(tmp_path):
    s = Store(tmp_path)
    s.put("candidate", "x:1", {"text": "  =CMD()"})
    s.put(
        "judgment",
        "j",
        dict(
            judgment_id="j",
            candidate_id="x",
            candidate_version=1,
            profile_id="p",
            audience_id="a",
            audience_version="1",
            execution_mode="mock",
            rubric_version="v",
            score_continuous=3.125,
            score_1_to_5=3,
            review_flags=[],
            status="scored",
        ),
    )
    value = export_results(s, "csv")
    assert "'  =CMD()" in value and "3.125" in value
