"""Original temporary fixtures, never provider calls or actual corpus observations.

The live-shaped envelopes below simulate persisted SDK results only to test the
post-freeze reader; they are not outputs of MockProvider or real Jev evidence.
"""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from jevtweet.contracts import (
    Candidate,
    Factor,
    JudgeRequest,
    Judgment,
    PredictionContext,
    canonical,
    digest,
    now,
)
from jevtweet.diagnostic_contracts import (
    DiagnosticAuthorization,
    DiagnosticSourceRecord,
    MetricSnapshot,
)
from jevtweet.diagnostic_report import write_diagnostic_report
from jevtweet.rubric import load_rubric
from jevtweet.scoring import score
from jevtweet.service import Service
from jevtweet.settings import Settings


def write_json(path, value):
    path.write_text(canonical(value) + "\n")


def write_lines(path, values):
    path.write_text("".join(canonical(value) + "\n" for value in values))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(directory, values=(1, 2, 3, 4), views=(10, 20, 30, 40), *, extra=1):
    stamp = now()
    rubric = load_rubric()
    initial = [f"original-{i}" for i in range(len(values))]
    ids = initial + [f"limited-{i}" for i in range(extra)]
    source_records = [
        DiagnosticSourceRecord(
            source_id="original-synthetic-test",
            candidate_id=candidate_id,
            source_record_index=i + 1,
            raw={"text": f"An original invented fixture about compiler garden {i}.", "date": "2025-01-04"},
            source_calendar_date="2025-01-04",
            source_date_precision="calendar_date_only",
            imported_at=stamp,
            post_type="original",
            media_presence="no" if candidate_id in initial else "yes",
            initial_cohort_eligible=candidate_id in initial,
            limitations=["unknown_observation_time"],
        ).model_dump(mode="json")
        for i, candidate_id in enumerate(ids)
    ]
    metrics = [
        MetricSnapshot(
            snapshot_id=f"snapshot-{i}",
            source_id="original-synthetic-test",
            candidate_id=candidate_id,
            imported_at=stamp,
            views=views[i] if i < len(views) else None,
            supplied_engagement_score=(i + 1) * 2,
        ).model_dump(mode="json")
        for i, candidate_id in enumerate(ids)
    ]
    write_lines(directory / "normalized_source.jsonl", source_records)
    write_lines(directory / "metric_snapshots.jsonl", metrics)
    manifest = {
        "source_id": "original-synthetic-test",
        "record_count": len(ids),
        "candidate_ids": ids,
        "eligible_candidate_ids": initial,
        "excluded_candidates": [
            {"candidate_id": candidate_id, "reason_codes": ["media_without_description"]}
            for candidate_id in ids[len(values) :]
        ],
        "artifacts": {
            name: {"sha256": sha(directory / name), "size_bytes": (directory / name).stat().st_size}
            for name in ("normalized_source.jsonl", "metric_snapshots.jsonl")
        },
    }
    write_json(directory / "source_manifest.json", manifest)
    service = Service(Settings(data_dir=directory / "store", account_dir=directory / "account"))
    requests, prepared = [], []
    for cid in initial:
        request = JudgeRequest(
            candidate=Candidate(
                candidate_id=cid, text="Original synthetic fixture.", content_available_at=stamp
            ),
            context=PredictionContext(prediction_cutoff=stamp, evaluation_split="development"),
            execution_mode="live",
        )
        audience, state, questions, _, fingerprint = service._prepare(request)
        requests.append(request.model_dump(mode="json"))
        prepared.append(
            {"candidate_id": cid, "state": state, "questions": questions, "input_hash": fingerprint}
        )
    write_lines(directory / "diagnostic_requests.jsonl", requests)
    write_lines(directory / "prepared_inputs.jsonl", prepared)
    protocol = {
        "protocol_version": "diagnostic_v1",
        "candidate_ids": initial,
        "source_manifest_sha256": sha(directory / "source_manifest.json"),
        "requests_sha256": sha(directory / "diagnostic_requests.jsonl"),
        "prepared_inputs_sha256": sha(directory / "prepared_inputs.jsonl"),
        "requests_file": "diagnostic_requests.jsonl",
        "model": "jev-1.13.0",
        "profile_id": "text_core_v1",
        "audience": {
            "audience_id": audience.audience_id,
            "version": audience.version,
            "snapshot": audience.model_dump(mode="json"),
            "hash": digest(audience),
        },
        "rubric_version": rubric["version"],
        "rubric_hash": digest(rubric),
        "code_commit": "synthetic-fixture-only",
        "sdk_version": "0.7.0",
    }
    protocol["protocol_hash"] = digest(protocol)
    write_json(directory / "protocol.json", protocol)
    authorization = DiagnosticAuthorization(
        protocol_hash=protocol["protocol_hash"],
        approved_by="Synthetic test owner",
        approval_note="Synthetic envelope fixture; no API requests.",
        pilot_ceiling_usd=0.1,
    ).model_dump(mode="json")
    write_json(directory / "authorization.json", authorization)
    judgments = []
    for candidate_id, value, item in zip(initial, values, prepared, strict=True):
        factors = {
            key: Factor(question_id=key, type="score", score=value, confidence=0.8)
            for key in rubric["profiles"]["text_core_v1"] + ["direct_overall"]
        }
        factors["aversion"].score = 0
        factors.update(
            assessability=Factor(
                question_id="assessability", type="choice", choice="assessable", confidence=0.9
            ),
            missing_context=Factor(question_id="missing_context", type="noul", noul=0),
            instruction_like=Factor(question_id="instruction_like", type="noul", noul=0),
        )
        judgment = Judgment(
            candidate_id=candidate_id,
            candidate_version=1,
            execution_mode="live",
            execution_status="succeeded",
            evidence_completeness="complete",
            profile_id="text_core_v1",
            audience_id="production_ai_coding",
            audience_version=audience.version,
            rubric_version=rubric["version"],
            model_requested=protocol["model"],
            model_returned=protocol["model"],
            input_hash=item["input_hash"],
            reference_set_hash=digest([]),
            code_commit="synthetic-fixture-only",
            provenance={"provider": "typesafe_sdk", "rubric_hash": digest(rubric)},
            **score(factors, "text_core_v1"),
        )
        judgments.append({"candidate_id": candidate_id, "judgment": judgment.model_dump(mode="json")})
    freeze = {
        "protocol_hash": protocol["protocol_hash"],
        "frozen_at": stamp.isoformat(),
        "execution_mode": "live",
        "records": judgments,
        "unexecuted_candidate_ids": [],
        "authorization_hash": digest(authorization),
    }
    freeze["freeze_hash"] = digest(freeze)
    write_json(directory / "judgment_freeze.json", freeze)
    return protocol, freeze


def refreeze(directory, freeze):
    freeze.pop("freeze_hash")
    freeze["freeze_hash"] = digest(freeze)
    write_json(directory / "judgment_freeze.json", freeze)


def test_missing_freeze_refuses_before_metrics_read(tmp_path, monkeypatch):
    fixture(tmp_path)
    (tmp_path / "judgment_freeze.json").unlink()
    original = Path.read_text
    read_paths = []

    def read(path, *args, **kwargs):
        read_paths.append(path.name)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(ValueError, match="freeze"):
        write_diagnostic_report(tmp_path)
    assert "metric_snapshots.jsonl" not in read_paths
    assert not (tmp_path / "diagnostic_report.json").exists()


@pytest.mark.parametrize(
    "file",
    [
        "protocol.json",
        "judgment_freeze.json",
        "source_manifest.json",
        "diagnostic_requests.jsonl",
        "prepared_inputs.jsonl",
        "authorization.json",
    ],
)
def test_rejects_tampered_bindings_before_metric_access(tmp_path, monkeypatch, file):
    fixture(tmp_path)
    path = tmp_path / file
    if file.endswith("jsonl"):
        path.write_text(path.read_text() + "{}\n")
    else:
        changed = json.loads(path.read_text())
        changed["unexpected_mutation"] = True
        write_json(path, changed)
    original = Path.read_text

    def guard_metrics(path, *args, **kwargs):
        assert path.name != "metric_snapshots.jsonl", "Invalid freeze must fail before opening metrics"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guard_metrics)
    with pytest.raises(ValueError):
        write_diagnostic_report(tmp_path)


@pytest.mark.parametrize(
    "change",
    [
        {"execution_mode": "mock"},
        {"provenance": {"provider": "deterministic_mock_v1"}},
        {"model_requested": "another-model"},
        {"model_returned": "another-model"},
        {"profile_id": "reference_enriched_v1"},
        {"audience_id": "another-audience"},
        {"rubric_version": "different-rubric"},
        {"code_commit": "different-code"},
        {"input_hash": "a" * 64},
        {"sdk_version": "another-sdk"},
    ],
)
def test_live_freeze_cannot_launder_mock_or_configuration_changes(tmp_path, change):
    _, freeze = fixture(tmp_path)
    freeze["records"][0]["judgment"].update(change)
    refreeze(tmp_path, freeze)
    with pytest.raises(ValueError):
        write_diagnostic_report(tmp_path)


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "extra", "overlap", "reorder"])
def test_freeze_requires_exact_fixed_cohort_membership(tmp_path, mutation):
    _, freeze = fixture(tmp_path)
    if mutation == "duplicate":
        freeze["records"].append(freeze["records"][0])
    elif mutation == "missing":
        freeze["records"].pop()
    elif mutation == "extra":
        freeze["unexecuted_candidate_ids"].append("unknown")
    elif mutation == "overlap":
        freeze["unexecuted_candidate_ids"].append(freeze["records"][0]["candidate_id"])
    else:
        freeze["records"].reverse()
    refreeze(tmp_path, freeze)
    with pytest.raises(ValueError):
        write_diagnostic_report(tmp_path)


def test_scored_subset_ranks_keep_ties_missingness_and_separate_baseline(tmp_path):
    fixture(tmp_path, values=(1, 1, 4, 3), views=(10, 10, 40, None))
    result = write_diagnostic_report(tmp_path)
    relation = result["associations"]["composite_vs_views"]
    assert relation["n"] == 3 and relation["spearman"] == pytest.approx(1)
    assert relation["missing_pairs"] == 1
    assert relation["x_ties"]["tied_records"] == 2
    assert relation["y_ties"]["tied_records"] == 2
    assert result["associations"]["direct_vs_views"]["n"] == 3
    assert "p_value" not in relation
    rows = [json.loads(line) for line in (tmp_path / "diagnostic_table.jsonl").read_text().splitlines()]
    assert len(rows) == 5
    assert rows[0]["source"]["raw"]["text"].startswith("An original invented fixture")
    assert rows[-1]["judgment"] is None
    assert rows[-1]["cohort_status"] == "excluded"
    assert rows[0]["metric_snapshot"]["observed_at"] is None
    assert rows[0]["certainty"]["by_question"]["relevance"] == 0.8


@pytest.mark.parametrize(
    "values,views,reason",
    [
        ((1, 1, 1), (1, 2, 3), "constant_x"),
        ((1, 2, 3), (4, 4, 4), "constant_y"),
        ((1, 2), (1, 2), "fewer_than_three_pairs"),
    ],
)
def test_undefined_correlations_are_null_not_zero(tmp_path, values, views, reason):
    fixture(tmp_path, values=values, views=views)
    relation = write_diagnostic_report(tmp_path)["associations"]["composite_vs_views"]
    assert relation["spearman"] is None
    assert reason in relation["undefined_reasons"]


def test_failure_partial_abstention_unexecuted_coverage_uses_both_denominators(tmp_path):
    _, freeze = fixture(tmp_path, values=(1, 2, 3, 4, 2), views=(1, 2, 3, 4, 5), extra=2)
    for i, status in enumerate(["failed", "partial", "abstained"]):
        judgment = freeze["records"][i]["judgment"]
        judgment.update(status=status, score_continuous=None, score_1_to_5=None)
    freeze["records"][0]["judgment"].update(
        factors={}, model_returned=None, execution_status="failed", error_category="network"
    )
    last = freeze["records"].pop()
    freeze["unexecuted_candidate_ids"] = [last["candidate_id"]]
    refreeze(tmp_path, freeze)
    report = write_diagnostic_report(tmp_path)
    coverage = report["coverage"]
    assert coverage["total_records"] == 7
    assert coverage["initial_cohort_records"] == 5
    assert coverage["status_counts"] == {
        "scored": 1,
        "partial": 1,
        "abstained": 1,
        "failed": 1,
        "unexecuted": 1,
    }
    assert coverage["fully_scored_fraction_of_all_records"] == pytest.approx(1 / 7)
    assert coverage["fully_scored_fraction_of_initial_cohort"] == pytest.approx(1 / 5)
    assert report["associations"]["composite_vs_views"]["n"] == 1


def test_disagreement_selection_rule_is_deterministic_and_carries_alternatives(tmp_path):
    fixture(tmp_path, values=(4, 3, 2, 1, 2, 3), views=(1, 2, 3, 4, 5, 6))
    report = write_diagnostic_report(tmp_path)
    cases = report["disagreements"]["largest_rank_gaps"]
    assert len(cases) == 3
    assert cases[0]["candidate_id"] == "original-0"
    assert [(-c["absolute_percentile_gap"], c["candidate_id"]) for c in cases] == sorted(
        (-c["absolute_percentile_gap"], c["candidate_id"]) for c in cases
    )
    assert report["disagreements"]["extreme_contrasts"]
    assert all(case["alternative_explanations"] for case in cases)
    assert "hypothesis" in report["construct_hypothesis"].lower()
    text = (tmp_path / "collection_requirements.md").read_text()
    assert "5,000" in text and "48-hour" in text and "final-test" in text


def test_duplicate_metrics_are_ambiguous_and_never_arbitrarily_selected(tmp_path):
    fixture(tmp_path)
    path = tmp_path / "metric_snapshots.jsonl"
    path.write_text(path.read_text() + path.read_text().splitlines()[0] + "\n")
    with pytest.raises(ValueError, match="metric"):
        write_diagnostic_report(tmp_path)


def test_direct_baseline_is_independent_of_the_composite(tmp_path):
    _, freeze = fixture(tmp_path)
    for record in freeze["records"]:
        record["judgment"]["factors"]["direct_overall"]["score"] = 2
    refreeze(tmp_path, freeze)
    report = write_diagnostic_report(tmp_path)
    assert report["associations"]["composite_vs_views"]["spearman"] == pytest.approx(1)
    assert report["associations"]["direct_vs_views"]["spearman"] is None
    assert "constant_x" in report["associations"]["direct_vs_views"]["undefined_reasons"]


def test_failed_envelope_with_semantic_answers_still_requires_pinned_model(tmp_path):
    _, freeze = fixture(tmp_path)
    freeze["records"][0]["judgment"].update(
        status="failed", execution_status="failed", model_returned=None, error_category="network"
    )
    refreeze(tmp_path, freeze)
    with pytest.raises(ValueError, match="approved model"):
        write_diagnostic_report(tmp_path)


def test_metric_source_identity_cannot_silently_mix_snapshots(tmp_path):
    fixture(tmp_path)
    path = tmp_path / "metric_snapshots.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    records[0]["source_id"] = "different-synthetic-source"
    write_lines(path, records)
    with pytest.raises(ValueError, match="metric snapshot identity|metric_snapshots"):
        write_diagnostic_report(tmp_path)


@pytest.mark.parametrize("name", ["normalized_source.jsonl", "metric_snapshots.jsonl"])
def test_source_artifact_tampering_is_rejected_after_freeze_before_join(tmp_path, name):
    fixture(tmp_path)
    path = tmp_path / name
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if name == "normalized_source.jsonl":
        rows[0]["raw"]["text"] = "Tampered original source text."
    else:
        rows[0]["views"] = 99999
    write_lines(path, rows)
    with pytest.raises(ValueError, match="artifact|checksum"):
        write_diagnostic_report(tmp_path)
    assert not (tmp_path / "diagnostic_report.json").exists()


def rebind_protocol(directory, protocol, freeze):
    protocol.pop("protocol_hash")
    protocol["protocol_hash"] = digest(protocol)
    write_json(directory / "protocol.json", protocol)
    authorization = json.loads((directory / "authorization.json").read_text())
    authorization["protocol_hash"] = protocol["protocol_hash"]
    write_json(directory / "authorization.json", authorization)
    freeze["protocol_hash"] = protocol["protocol_hash"]
    freeze["authorization_hash"] = digest(authorization)
    refreeze(directory, freeze)


@pytest.mark.parametrize("mutation", ["input_id", "request_mode", "request_profile", "request_version"])
def test_rehashed_input_lineage_changes_are_rejected_before_metrics(tmp_path, monkeypatch, mutation):
    protocol, freeze = fixture(tmp_path)
    name = "prepared_inputs.jsonl" if mutation == "input_id" else "diagnostic_requests.jsonl"
    path = tmp_path / name
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if mutation == "input_id":
        rows[0]["candidate_id"] = "other-candidate"
    elif mutation == "request_mode":
        rows[0]["execution_mode"] = "mock"
    elif mutation == "request_profile":
        rows[0]["profile_id"] = "reference_enriched_v1"
    else:
        rows[0]["candidate"]["candidate_version"] = 2
    write_lines(path, rows)
    key = "prepared_inputs_sha256" if mutation == "input_id" else "requests_sha256"
    protocol[key] = sha(path)
    rebind_protocol(tmp_path, protocol, freeze)
    original_read_text, original_read_bytes = Path.read_text, Path.read_bytes

    def text_guard(path, *args, **kwargs):
        assert path.name != "metric_snapshots.jsonl", "Invalid lineage must fail before reading metrics"
        return original_read_text(path, *args, **kwargs)

    def bytes_guard(path, *args, **kwargs):
        assert path.name != "metric_snapshots.jsonl", "Invalid lineage must fail before hashing metrics"
        return original_read_bytes(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", text_guard)
    monkeypatch.setattr(Path, "read_bytes", bytes_guard)
    with pytest.raises(ValueError):
        write_diagnostic_report(tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("one_failure", [False, True])
async def test_complete_synthetic_prepare_freeze_report_loop_is_offline(tmp_path, monkeypatch, one_failure):
    from test_diagnostic_ingest import source_row, write_source

    from jevtweet import diagnostics
    from jevtweet.contracts import ProviderResult
    from jevtweet.provider import ProviderError, TypeSafeProvider

    # Explicit injection simulates transport mechanics. Neither these fabricated
    # envelopes nor their report constitute real Jev judgments or evidence.
    class LocalEnvelopeFixture:
        def __init__(self):
            self.states = []

        async def ask(self, state, questions):
            self.states.append(state)
            if one_failure and len(self.states) == 2:
                raise ProviderError("synthetic_transport_failure", retryable=False)
            factors = {}
            for key, spec in questions.items():
                if spec["type"] == "score":
                    factors[key] = Factor(
                        question_id=key,
                        type="score",
                        score=0 if key == "aversion" else len(self.states),
                        confidence=0.8,
                    )
                elif spec["type"] == "choice":
                    factors[key] = Factor(question_id=key, type="choice", choice="assessable", confidence=0.9)
                else:
                    factors[key] = Factor(question_id=key, type="noul", noul=0)
            return ProviderResult(
                factors=factors,
                model_returned="jev-1.13.0",
                usage={"input_tokens": 0},
                diagnostics={"synthetic_test_fixture": True},
            )

    async def no_real_provider(*args, **kwargs):
        pytest.fail("No real provider may be contacted by a synthetic integration test")

    monkeypatch.setattr(TypeSafeProvider, "ask", no_real_provider)
    monkeypatch.setattr(diagnostics, "code_commit", lambda: "a" * 40)
    monkeypatch.setattr("jevtweet.service.code_commit", lambda: "a" * 40)
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-local-fixture-not-a-credential")
    directory = tmp_path / "pilot"
    source = write_source(
        tmp_path / "invented.csv",
        [
            source_row(f"An original synthetic parser terrarium number {i}.", **{"Views": str(80 + i)})
            for i in range(3)
        ]
        + [source_row("An original synthetic image fixture.", **{"Has Media": "Yes"})],
    )
    settings = Settings(
        data_dir=tmp_path / "unused",
        account_dir=tmp_path / "isolated-account",
        spend_limit_usd=1,
        max_attempts=1,
    )
    prepared = diagnostics.prepare_diagnostic(source, directory, settings=settings, seed=13)
    assert prepared["initial_cohort_records"] == 3
    assert not (directory / "judgment_freeze.json").exists()
    with pytest.raises(ValueError, match="freeze"):
        write_diagnostic_report(directory)
    diagnostics.authorize_diagnostic(
        directory,
        pilot_ceiling_usd=0.1,
        approved_by="Synthetic test owner",
        approval_note="Test mechanics only; no actual spending or API calls.",
    )
    provider = LocalEnvelopeFixture()
    service = Service(replace(settings, data_dir=directory / "store"), provider=provider)
    original_read_text, original_read_bytes = Path.read_text, Path.read_bytes

    def check_no_metrics_during_execution(path):
        assert path.name not in {"metric_snapshots.jsonl", "normalized_source.jsonl", "source.csv"}, (
            "Judgment execution must not inspect outcomes or raw source rows"
        )

    def text_guard(path, *args, **kwargs):
        check_no_metrics_during_execution(path)
        return original_read_text(path, *args, **kwargs)

    def bytes_guard(path, *args, **kwargs):
        check_no_metrics_during_execution(path)
        return original_read_bytes(path, *args, **kwargs)

    with monkeypatch.context() as execution_guard:
        execution_guard.setattr(Path, "read_text", text_guard)
        execution_guard.setattr(Path, "read_bytes", bytes_guard)
        execution = await diagnostics.run_diagnostic(directory, settings=settings, service=service)
    assert execution["status"] == "frozen" and execution["metrics_joined"] is False
    assert len(provider.states) == 3
    frozen_bytes = (directory / "judgment_freeze.json").read_bytes()
    report = write_diagnostic_report(directory)
    assert report["coverage"]["total_records"] == 4
    assert report["coverage"]["initial_cohort_records"] == 3
    assert report["coverage"]["fully_scored_records"] == 3 - int(one_failure)
    assert report["coverage"]["status_counts"]["failed"] == int(one_failure)
    assert (directory / "judgment_freeze.json").read_bytes() == frozen_bytes
    assert report["predictor_promoted"] is report["predictive_accuracy_established"] is False
    repeat = await diagnostics.run_diagnostic(directory, settings=settings, service=service)
    assert repeat["status"] == "already_frozen" and len(provider.states) == 3
    assert not service.store.list("outcome")
    assert not service.store.list("predictor")
