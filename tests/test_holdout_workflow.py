"""Regression coverage for deliberately opening an immutable real final holdout."""

from copy import deepcopy

from jevtweet import evaluation as ev
from jevtweet.storage import Store


def real_rows():
    rows = ev._synthetic_rows(160)
    for row in rows:
        row.update(
            synthetic=False,
            execution_mode="live",
            model_requested=ev.Settings.model,
            model_returned=ev.Settings.model,
            sampling_provenance="prospective_enrollment_v1",
        )
    return rows


def test_real_evaluate_without_declarations_never_consumes_holdout(tmp_path, monkeypatch):
    rows = real_rows()
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
    report = ev.evaluate(store)
    assert store.list("holdout") == []
    assert report["status"] != "evaluated"
    assert not report.get("metrics")
