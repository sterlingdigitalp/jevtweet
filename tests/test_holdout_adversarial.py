"""Entirely invented temporary records exercising real-workflow boundaries.

These records have live-mode metadata only to test the local gate machinery;
they are never real provider calls, collected outcomes or predictive evidence.
"""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Barrier, Lock

import pytest
from test_evaluation import attach_data

from jevtweet import evaluation as ev
from jevtweet.storage import Store


def insert_fixture_row(store, template_judgment, template_run, row):
    judgment = deepcopy(template_judgment)
    run = deepcopy(template_run)
    candidate = deepcopy(run["request"]["candidate"])
    candidate.update(
        candidate_id=row["candidate_id"],
        candidate_version=int(row["key"].rsplit(":", 1)[1]),
        text=row["text"],
        author_id=row["author"],
        published_at=row["cutoff"],
        content_available_at=row["cutoff"],
        provenance="invented_unit_test_fixture",
        thread_id=row.get("thread_id"),
    )
    context = {"prediction_cutoff": row["cutoff"]}
    judgment.update(
        judgment_id=row["judgment_id"],
        candidate_id=candidate["candidate_id"],
        candidate_version=candidate["candidate_version"],
        created_at=row["cutoff"],
        score_continuous=row["editorial"],
        factors={
            name: {"score": row["features"][name], "assessability": "assessable"}
            for name in ev.SEMANTIC_NAMES
        },
    )
    judgment["factors"]["overall"] = {"score": row["direct"], "assessability": "assessable"}
    run.update(judgment_id=judgment["judgment_id"], state={"candidate": {"text": candidate["text"]}})
    run["request"].update(candidate=candidate, context=context)
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
    observed = (ev._date(row["cutoff"]) + timedelta(hours=48)).isoformat()
    observation = {
        "observation_id": f"outcome-{row['judgment_id']}",
        "candidate_id": candidate["candidate_id"],
        "candidate_version": candidate["candidate_version"],
        "source": "manual_native_views",
        "metric": "views",
        "distribution": "organic",
        "views": 15000 if row["label"] else 500,
        "elapsed_hours": 48,
        "observed_at": observed,
        "available_at": observed,
        "synthetic": False,
    }
    store.put("candidate", ev._key(candidate), candidate)
    store.put("judgment", judgment["judgment_id"], judgment)
    store.put("run", judgment["judgment_id"], run)
    store.put("outcome", observation["observation_id"], observation)


@pytest.fixture
def invented_real_workflow(tmp_path, monkeypatch):
    # A small mechanics fixture is sufficient here; production gates remain
    # unchanged and are independently tested against their versioned values.
    policy = dict(
        ev.CONFIG["promotion_policy"],
        minimum_eligible_rows=120,
        minimum_test_rows=12,
        minimum_test_author_clusters=5,
    )
    monkeypatch.setitem(ev.CONFIG, "promotion_policy", policy)
    store = Store(tmp_path)
    template_judgment = attach_data(store)
    template_run = store.get("run", "judgment")
    with store.connect() as db:
        db.execute("DELETE FROM records")
    rows = ev._synthetic_rows(200)
    for row in rows:
        insert_fixture_row(store, template_judgment, template_run, row)
    options = {
        "synthetic": False,
        "task": "absolute_48h_v1",
        "representative_sampling": True,
        "sampling_declaration": "Invented unit-test enrollment; no research or prediction claim",
        "comparison_population": "Invented unit-test population",
        "cohort": {
            "profile_id": template_judgment["profile_id"],
            "audience_id": template_judgment["audience_id"],
            "audience_version": template_judgment["audience_version"],
            "rubric_version": template_judgment["rubric_version"],
            "model_requested": template_judgment["model_requested"],
            "execution_mode": "live",
            "outcome_source": "manual_native_views",
        },
    }
    return store, rows, options, template_judgment, template_run


def test_preflight_does_not_label_final_rows_or_write_any_evaluation_records(
    invented_real_workflow, monkeypatch
):
    store, rows, options, _, _ = invented_real_workflow
    final_ids = {row["candidate_id"] for row in rows[160:]}
    original = ev.label_candidate
    inspected = set()

    def check(candidate, *args, **kwargs):
        assert candidate["candidate_id"] not in final_ids, "Preflight inspected a final-test target"
        inspected.add(candidate["candidate_id"])
        return original(candidate, *args, **kwargs)

    monkeypatch.setattr(ev, "label_candidate", check)
    before = {kind: deepcopy(store.list(kind)) for kind in ("holdout", "experiment")}
    report = ev.preflight(store, **options)
    assert inspected
    assert report["final_test_opened"] is False and report["holdout_consumed"] is False
    assert all(store.list(kind) == records for kind, records in before.items())


@pytest.mark.parametrize("field,value", [("max_rows", 39), ("max_rows", 5001), ("task", "invalid-task")])
def test_invalid_preflight_configuration_cannot_reserve_a_holdout(invented_real_workflow, field, value):
    store, _, options, _, _ = invented_real_workflow
    options[field] = value
    with pytest.raises(ValueError):
        ev.freeze_evaluation(store, **options)
    assert not store.list("holdout") and not store.list("experiment")


def test_purged_final_member_stays_protected_and_unopened_labels_are_null(invented_real_workflow):
    store, rows, options, judgment, run = invented_real_workflow
    duplicate = dict(rows[-1], text=rows[0]["text"])
    with store.connect() as db:
        for kind, record_id in (
            ("candidate", duplicate["key"]),
            ("judgment", duplicate["judgment_id"]),
            ("run", duplicate["judgment_id"]),
            ("outcome", f"outcome-{duplicate['judgment_id']}"),
        ):
            db.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, record_id))
    insert_fixture_row(store, judgment, run, duplicate)

    data = ev.prepare_development(store, task=options["task"], cohort=options["cohort"])

    assert duplicate["key"] in data["protected_test_keys"]
    assert all(row["label"] is None for row in data["test_input_rows"])


def test_readiness_keeps_failed_and_missing_judgments_in_collection_denominator(invented_real_workflow):
    from jevtweet.readiness import development_readiness

    store, _, options, _, _ = invented_real_workflow
    baseline = ev.prepare_development(store, task=options["task"], cohort=options["cohort"])
    failed, missing = baseline["rows"][:2]
    judgment = store.get("judgment", failed["judgment_id"])
    judgment.update(status="failed", score_continuous=None)
    store.put("judgment", judgment["judgment_id"], judgment, replace=True)
    with store.connect() as db:
        for kind in ("judgment", "run"):
            db.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, missing["judgment_id"]))

    report = development_readiness(store, **options)

    assert report["observed"]["development_candidates"] == baseline["development_candidate_count"] == 160
    assert report["observed"]["eligible_rows"] == len(baseline["rows"]) - 2
    assert report["observed"]["retention_rate"] == (len(baseline["rows"]) - 2) / 160
    assert report["observed"]["failure_coverage"]["candidates_with_failed_attempts"] == 1
    assert report["test_outcomes_inspected"] is False and not store.list("holdout")


def test_frozen_holdout_cannot_move_into_development_after_growth_or_aliases(
    invented_real_workflow, monkeypatch
):
    store, rows, options, judgment, run = invented_real_workflow
    frozen = ev.freeze_evaluation(store, **options)
    assert frozen["status"] == "frozen", frozen.get("not_ready_reasons")
    protected = set(frozen["protected_test_keys"])
    protected_ids = {key.rsplit(":", 1)[0] for key in protected}
    held = next(row for row in rows if row["key"] in protected)
    earliest = (ev._date(rows[0]["cutoff"]) - timedelta(days=6)).isoformat()
    aliases = [
        dict(
            held,
            key="rekeyed-heldout:1",
            candidate_id="rekeyed-heldout",
            judgment_id="alias-rekey",
            cutoff=earliest,
        ),
        dict(held, key=f"{held['candidate_id']}:2", judgment_id="alias-version", cutoff=earliest),
        dict(
            held,
            key="near-heldout:1",
            candidate_id="near-heldout",
            judgment_id="alias-near",
            text=held["text"] + " extra",
            cutoff=earliest,
        ),
    ]
    for alias in aliases:
        insert_fixture_row(store, judgment, run, alias)
        protected_ids.add(alias["candidate_id"])
    additions = ev._synthetic_rows(400)[200:]
    for row in additions:
        insert_fixture_row(store, judgment, run, row)
    original = ev.label_candidate

    def check(candidate, *args, **kwargs):
        assert candidate["candidate_id"] not in protected_ids, "Protected identity entered development"
        return original(candidate, *args, **kwargs)

    monkeypatch.setattr(ev, "label_candidate", check)
    development = ev.prepare_development(store, task=options["task"], cohort=options["cohort"])
    assert not {r["candidate_id"] for r in development["rows"]} & protected_ids
    assert store.list("holdout") == []  # Freezing protects membership without opening observations.


@pytest.mark.parametrize("mutation", ["comparison_population", "sampling_declaration", "source"])
def test_changed_frozen_declaration_is_rejected_before_any_test_consumption(invented_real_workflow, mutation):
    store, _, options, _, _ = invented_real_workflow
    frozen = ev.freeze_evaluation(store, **options)
    assert frozen["status"] == "frozen", frozen.get("not_ready_reasons")
    report = deepcopy(frozen)
    if mutation == "source":
        report["declarations"]["cohort"]["outcome_source"] = "rewritten-source"
    else:
        report["declarations"][mutation] = "retrospective rewrite"
    store.put("experiment", report["experiment_id"], report, replace=True)

    with pytest.raises(ValueError, match="[Ff]rozen|immutable"):
        ev.open_holdout(store, frozen["experiment_id"], frozen_candidate_hash=frozen["frozen_candidate_hash"])
    assert not store.list("holdout")


def test_two_concurrent_open_requests_consume_and_label_a_real_holdout_once(
    invented_real_workflow, monkeypatch
):
    store, _, options, _, _ = invented_real_workflow
    frozen = ev.freeze_evaluation(store, **options)
    assert frozen["status"] == "frozen", frozen.get("not_ready_reasons")
    original = ev.label_candidate
    observed = []
    lock = Lock()

    def track(candidate, *args, **kwargs):
        with lock:
            observed.append(ev._key(candidate))
        return original(candidate, *args, **kwargs)

    monkeypatch.setattr(ev, "label_candidate", track)
    barrier = Barrier(2)

    def open_once(_):
        barrier.wait(timeout=10)
        return ev.open_holdout(
            store, frozen["experiment_id"], frozen_candidate_hash=frozen["frozen_candidate_hash"]
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(open_once, range(2)))
    assert sorted(result["status"] for result in results) == ["evaluated", "holdout_already_consumed"]
    assert observed and len(observed) == len(set(observed))
    assert len(store.list("holdout")) == 1
    assert not store.list("predictor")


def test_open_never_loads_another_holdout_created_after_its_own_freeze(invented_real_workflow, monkeypatch):
    from jevtweet import evaluation_workflow as workflow

    store, _, options, _, _ = invented_real_workflow
    frozen = ev.freeze_evaluation(store, **options)
    assert frozen["status"] == "frozen", frozen.get("not_ready_reasons")
    identity = ev._holdout_identity(
        {"candidate_id": "other-protected", "key": "other-protected:1", "text": "separate private content"}
    )
    store.put(
        "evaluation_freeze",
        "later-other-freeze",
        {"protected_test_keys": [identity["key"]], "protected_test_identities": [identity]},
    )
    marker = "other_final_outcome_must_not_be_decoded"
    store.put(
        "outcome",
        "other-protected-outcome",
        {"candidate_id": identity["candidate_id"], "candidate_version": 1, "private_marker": marker},
    )
    original = workflow.json.loads

    def refuse_other_outcome(body, *args, **kwargs):
        assert marker not in body, "Opening one holdout decoded another holdout's outcome"
        return original(body, *args, **kwargs)

    monkeypatch.setattr(workflow.json, "loads", refuse_other_outcome)
    result = ev.open_holdout(
        store, frozen["experiment_id"], frozen_candidate_hash=frozen["frozen_candidate_hash"]
    )
    assert result["status"] == "evaluated"


@pytest.mark.parametrize("change", ["earlier_corpus_growth", "rekey", "new_version", "near_duplicate"])
def test_preflight_exposed_development_cannot_later_be_presented_as_untouched_final_test(
    invented_real_workflow, change
):
    store, rows, options, judgment, run = invented_real_workflow
    initial = ev.preflight(store, **options)
    assert initial["ready_to_freeze"], initial["not_ready_reasons"]
    assert not store.list("holdout") and not store.list("experiment")
    if change == "earlier_corpus_growth":
        additions = ev._synthetic_rows(600)[200:]
        for row in additions:
            row["cutoff"] = (ev._date(row["cutoff"]) - timedelta(days=4000)).isoformat()
            insert_fixture_row(store, judgment, run, row)
    else:
        exposed = next(row for row in rows if row["key"] in initial["split_manifest"]["partitions"]["train"])
        alias = dict(
            exposed,
            key="exposed-alias:1",
            candidate_id="exposed-alias",
            judgment_id="exposed-alias-judgment",
            cutoff=(ev._date(rows[-1]["cutoff"]) + timedelta(days=3)).isoformat(),
        )
        if change == "new_version":
            alias.update(key=f"{exposed['candidate_id']}:2", candidate_id=exposed["candidate_id"])
        elif change == "near_duplicate":
            alias["text"] += " extra"
        insert_fixture_row(store, judgment, run, alias)

    result = ev.freeze_evaluation(store, **options)

    assert result["status"] == "not_ready", "Previously inspected development evidence became a final holdout"
    assert any("expos" in reason.lower() for reason in result["not_ready_reasons"]), result[
        "not_ready_reasons"
    ]
    assert not store.list("holdout") and not store.list("evaluation_freeze")


@pytest.mark.parametrize("entry_point", ["readiness", "development_evaluation"])
def test_other_development_entry_points_also_prevent_later_reuse_as_final(
    invented_real_workflow, entry_point
):
    from jevtweet.readiness import development_readiness

    store, rows, options, judgment, run = invented_real_workflow
    if entry_point == "readiness":
        observed = development_readiness(store, **options)
        assert observed["test_outcomes_inspected"] is False
    else:
        observed = ev.evaluate(store, **options)
        assert observed["status"] == "development_only"
    assert not store.list("holdout") and not store.list("evaluation_freeze")
    alias = dict(
        rows[20],
        key="later-exposed-alias:1",
        candidate_id="later-exposed-alias",
        judgment_id="later-exposed-judgment",
        cutoff=(ev._date(rows[-1]["cutoff"]) + timedelta(days=3)).isoformat(),
    )
    insert_fixture_row(store, judgment, run, alias)

    result = ev.freeze_evaluation(store, **options)

    assert result["status"] == "not_ready"
    assert any("expos" in reason.lower() for reason in result["not_ready_reasons"]), result[
        "not_ready_reasons"
    ]
    assert not store.list("holdout") and not store.list("evaluation_freeze")
