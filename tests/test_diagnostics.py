"""Diagnostic orchestration uses invented CSV and an injected provider, never the network."""

import csv
import json
from dataclasses import replace

import pytest

from jevtweet import diagnostics
from jevtweet.contracts import digest
from jevtweet.settings import Settings

COMMIT = "a" * 40


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "code_commit", lambda: COMMIT)
    monkeypatch.setattr("jevtweet.service.code_commit", lambda: COMMIT)
    source = tmp_path / "invented.csv"
    fields = [
        "Date",
        "Post Type",
        "Full Text",
        "Views",
        "Likes",
        "Reposts",
        "Quotes",
        "Replies",
        "Bookmarks",
        "Engagement Score",
        "Has Media",
        "Theme",
        "Hook (first line)",
        "Word Count",
        "Char Count",
        "Rank",
    ]
    with source.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for i in range(4):
            text = f"Original invented parser garden {i}, with a tiny compiler. 🌱"
            row = dict.fromkeys(fields, "")
            row.update(
                {
                    "Date": "2025-01-03",
                    "Post Type": "Original",
                    "Full Text": text,
                    "Views": str(100 + i),
                    "Likes": "2",
                    "Reposts": "1",
                    "Quotes": "0",
                    "Replies": "0",
                    "Bookmarks": "0",
                    "Engagement Score": "4",
                    "Has Media": "Yes" if i == 3 else "No",
                    "Theme": "invented-only",
                    "Hook (first line)": "invented",
                    "Word Count": str(len(text.split())),
                    "Char Count": str(len(text)),
                    "Rank": str(i + 1),
                }
            )
            writer.writerow(row)
    settings = Settings(data_dir=tmp_path / "unused", account_dir=tmp_path / "account", spend_limit_usd=0)
    directory = tmp_path / "pilot"
    result = diagnostics.prepare_diagnostic(source, directory, settings=settings, seed=23)
    return directory, settings, result, source


def test_preparation_is_offline_blind_seeded_and_idempotent(prepared, monkeypatch):
    directory, settings, result, source = prepared
    protocol = json.loads((directory / "protocol.json").read_text())
    assert result["status"] == "prepared_awaiting_authorization"
    assert len(protocol["candidate_ids"]) == 3
    assert protocol["approved_spending_limit_usd"] == 0
    assert protocol["estimate"]["logical_requests"] == 3
    assert protocol["estimate"]["maximum_provider_attempts"] == 9
    assert protocol["estimate"]["maximum_reserved_usd"] > 0
    assert not (directory / "authorization.json").exists()
    assert not (directory / "judgment_freeze.json").exists()
    inputs = [json.loads(line) for line in (directory / "prepared_inputs.jsonl").read_text().splitlines()]
    forbidden = {"views", "likes", "rank", "theme", "hook_type", "engagement_score", "filename", "source_id"}

    def check(value):
        if isinstance(value, dict):
            assert not forbidden.intersection(value)
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)

    for item in inputs:
        check(item["state"])
        assert item["state"]["candidate"]["text"].startswith("Original invented")
    first = (directory / "protocol.json").read_bytes()
    diagnostics.prepare_diagnostic(source, directory, settings=settings, seed=23)
    assert (directory / "protocol.json").read_bytes() == first
    with pytest.raises(ValueError, match="seed|protocol"):
        diagnostics.prepare_diagnostic(source, directory, settings=settings, seed=24)


@pytest.mark.asyncio
async def test_prior_account_budget_does_not_authorize_pilot(prepared, monkeypatch):
    directory, settings, _, _ = prepared
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-not-a-credential")
    with pytest.raises(ValueError, match="authorization"):
        await diagnostics.run_diagnostic(directory, settings=replace(settings, spend_limit_usd=1))
    assert not (directory / "judgment_freeze.json").exists()


def test_request_integrity_and_runtime_drift_precede_calls(prepared):
    directory, settings, _, _ = prepared
    path = directory / "diagnostic_requests.jsonl"
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="integrity|hash"):
        diagnostics.authorize_diagnostic(
            directory, pilot_ceiling_usd=0.1, approved_by="Synthetic owner", approval_note="Test only"
        )


def test_estimate_uses_runtime_reservation_arithmetic(prepared):
    from jevtweet.request_budget import request_budget

    directory, settings, _, _ = prepared
    protocol = json.loads((directory / "protocol.json").read_text())
    inputs = [json.loads(line) for line in (directory / "prepared_inputs.jsonl").read_text().splitlines()]
    budgets = [request_budget(item["state"], item["questions"], settings) for item in inputs]
    assert protocol["estimate"]["initial_reserved_usd"] == pytest.approx(
        sum(b["per_attempt_usd"] for b in budgets)
    )
    assert protocol["estimate"]["maximum_reserved_usd"] == pytest.approx(
        sum(b["per_attempt_usd"] * settings.max_attempts for b in budgets)
    )
    assert protocol["protocol_hash"] == digest({k: v for k, v in protocol.items() if k != "protocol_hash"})


class SimulatedSDKProvider:
    """Explicit local wire test double. These outputs are not Jev research evidence."""

    def __init__(self):
        self.calls = 0

    async def ask(self, state, questions):
        from jevtweet.provider import MockProvider

        self.calls += 1
        response = await MockProvider().ask(state, questions)
        response.model_returned = "jev-1.13.0"
        response.usage = {"input_tokens": 100}
        return response


def authorize_and_service(prepared, monkeypatch, provider=None):
    from jevtweet.service import Service

    directory, settings, _, _ = prepared
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-wire-test-not-a-credential")
    diagnostics.authorize_diagnostic(
        directory,
        pilot_ceiling_usd=0.1,
        approved_by="Synthetic test owner",
        approval_note="Offline test fixture only",
    )
    settings = replace(settings, data_dir=directory / "store", spend_limit_usd=1)
    provider = provider or SimulatedSDKProvider()
    return settings, Service(settings, provider=provider), provider


@pytest.mark.asyncio
async def test_first_results_freeze_without_metric_reads_and_never_rerun(prepared, monkeypatch):
    from pathlib import Path

    directory, _, _, _ = prepared
    settings, service, provider = authorize_and_service(prepared, monkeypatch)
    original = Path.read_text

    def read(path, *args, **kwargs):
        assert path.name not in {"metric_snapshots.jsonl", "normalized_source.jsonl", "source.csv"}
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    result = await diagnostics.run_diagnostic(directory, settings=settings, service=service)
    assert result["status"] == "frozen" and result["metrics_joined"] is False
    assert result["record_count"] == provider.calls == 3
    freeze = (directory / "judgment_freeze.json").read_bytes()
    assert service.store.spending()["charged_or_reserved_usd"] == pytest.approx(3 * 100 * 0.042 / 1e6)
    assert (await diagnostics.run_diagnostic(directory, settings=settings, service=service))[
        "status"
    ] == "already_frozen"
    assert provider.calls == 3
    assert (directory / "judgment_freeze.json").read_bytes() == freeze


@pytest.mark.asyncio
async def test_mock_cannot_be_frozen_as_live_evidence(prepared, monkeypatch):
    from jevtweet.provider import MockProvider

    directory, _, _, _ = prepared
    settings, service, _ = authorize_and_service(prepared, monkeypatch, MockProvider())
    with pytest.raises(ValueError, match="model identity"):
        await diagnostics.run_diagnostic(directory, settings=settings, service=service)
    assert not (directory / "judgment_freeze.json").exists()


@pytest.mark.asyncio
async def test_crash_checkpoint_blocks_duplicate_unknown_call(prepared, monkeypatch):
    directory, _, _, _ = prepared
    settings, service, provider = authorize_and_service(prepared, monkeypatch)
    protocol = json.loads((directory / "protocol.json").read_text())
    cid = protocol["candidate_ids"][0]
    service.store.put("diagnostic_start", cid, {"reservation_id": "uncertain-test-reservation"})
    with pytest.raises(ValueError, match="Interrupted request"):
        await diagnostics.run_diagnostic(directory, settings=settings, service=service)
    assert provider.calls == 0
    assert not (directory / "judgment_freeze.json").exists()


@pytest.mark.asyncio
async def test_persisted_first_result_recovers_after_checkpoint_crash(prepared, monkeypatch):
    directory, _, _, _ = prepared
    settings, service, provider = authorize_and_service(prepared, monkeypatch)
    original = service.store.put
    once = True

    def crash(kind, key, value, **kwargs):
        nonlocal once
        if kind == "diagnostic_first" and once:
            once = False
            raise RuntimeError("Simulated process stop after persisted service result")
        return original(kind, key, value, **kwargs)

    monkeypatch.setattr(service.store, "put", crash)
    with pytest.raises(RuntimeError, match="Simulated"):
        await diagnostics.run_diagnostic(directory, settings=settings, service=service)
    assert provider.calls == 1
    monkeypatch.setattr(service.store, "put", original)
    result = await diagnostics.run_diagnostic(directory, settings=settings, service=service)
    assert result["record_count"] == provider.calls == 3
    assert result["requests_made"] == 2


@pytest.mark.asyncio
async def test_configuration_and_budget_fail_before_provider(prepared, monkeypatch):
    directory, _, _, _ = prepared
    settings, service, provider = authorize_and_service(prepared, monkeypatch)
    with pytest.raises(ValueError, match="runtime"):
        await diagnostics.run_diagnostic(
            directory, settings=replace(settings, max_attempts=1), service=service
        )
    tiny = replace(settings, spend_limit_usd=0.00000001)
    from jevtweet.service import Service

    with pytest.raises(ValueError, match="Shared-account"):
        await diagnostics.run_diagnostic(directory, settings=tiny, service=Service(tiny, provider=provider))
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_service_failure_retries_are_bounded_without_outer_retry(prepared, monkeypatch):
    from jevtweet.provider import ProviderError

    class TransientFailure:
        def __init__(self):
            self.calls = 0

        async def ask(self, state, questions):
            self.calls += 1
            raise ProviderError("timeout", retryable=True, retry_after=0)

    directory, _, _, _ = prepared
    settings, service, provider = authorize_and_service(prepared, monkeypatch, TransientFailure())
    result = await diagnostics.run_diagnostic(directory, settings=settings, service=service)
    assert provider.calls == 9 and result["record_count"] == 3
    records = json.loads((directory / "judgment_freeze.json").read_text())["records"]
    assert all(r["judgment"]["status"] == "failed" and r["judgment"]["attempt_count"] == 3 for r in records)
    await diagnostics.run_diagnostic(directory, settings=settings, service=service)
    assert provider.calls == 9


@pytest.mark.asyncio
async def test_wrong_model_failure_cannot_be_frozen(prepared, monkeypatch):
    from jevtweet.contracts import ProviderResult

    class WrongModel:
        async def ask(self, state, questions):
            return ProviderResult(model_returned="unexpected-model", error_category="model_mismatch")

    directory, _, _, _ = prepared
    settings, service, _ = authorize_and_service(prepared, monkeypatch, WrongModel())
    with pytest.raises(ValueError, match="model identity"):
        await diagnostics.run_diagnostic(directory, settings=settings, service=service)
    assert not (directory / "judgment_freeze.json").exists()
