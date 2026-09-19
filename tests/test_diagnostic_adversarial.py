"""Offline crash/rate-limit regressions for the frozen diagnostic runner."""

import asyncio
import csv
import json
import time
from dataclasses import replace

import pytest

from jevtweet import diagnostics
from jevtweet.provider import MockProvider
from jevtweet.service import Service
from jevtweet.settings import Settings


class OfflineFixtureProvider:
    """Deliberate injected wire fixture; this is never evidence from real Jev."""

    execution_mode = "live"

    def __init__(self, model):
        self.model = model
        self.calls = 0

    async def ask(self, state, questions):
        self.calls += 1
        result = await MockProvider().ask(state, questions)
        result.model_returned = self.model
        return result


@pytest.fixture
def setup_pilot(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "code_commit", lambda: "b" * 40)
    monkeypatch.setattr("jevtweet.service.code_commit", lambda: "b" * 40)
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline-fixture-placeholder")

    def build(**changes):
        fields = [
            "Rank",
            "Date",
            "Post Type",
            "Theme",
            "Hook (first line)",
            "Full Text",
            "Likes",
            "Reposts",
            "Quotes",
            "Replies",
            "Bookmarks",
            "Views",
            "Has Media",
            "Engagement Score",
            "Word Count",
            "Char Count",
        ]
        source = tmp_path / "invented.csv"
        with source.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for index in range(2):
                text = f"Invented pottery debugging note {index}."
                row = dict.fromkeys(fields, "")
                row.update(
                    {
                        "Rank": str(index + 1),
                        "Date": "2025-01-01",
                        "Post Type": "original",
                        "Full Text": text,
                        "Has Media": "No",
                        "Views": str(index),
                        "Word Count": str(len(text.split())),
                        "Char Count": str(len(text)),
                    }
                )
                writer.writerow(row)
        directory = tmp_path / "pilot"
        settings = Settings(
            data_dir=directory / "store",
            account_dir=tmp_path / "account",
            spend_limit_usd=1,
            max_attempts=1,
            **changes,
        )
        diagnostics.prepare_diagnostic(source, directory, settings=settings)
        diagnostics.authorize_diagnostic(
            directory, pilot_ceiling_usd=1, approved_by="Fixture owner", approval_note="Offline test only"
        )
        provider = OfflineFixtureProvider(settings.model)
        service = Service(replace(settings, data_dir=directory / "store"), provider=provider)
        return directory, settings, provider, service

    return build


@pytest.mark.asyncio
async def test_database_freeze_recovers_if_file_publication_is_interrupted(setup_pilot, monkeypatch):
    directory, settings, provider, service = setup_pilot()
    original = diagnostics._write_once

    def interrupt_freeze_file(path, *args, **kwargs):
        if path.name == "judgment_freeze.json":
            raise RuntimeError("Simulated process interruption before freeze file publication")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(diagnostics, "_write_once", interrupt_freeze_file)
    with pytest.raises(RuntimeError, match="Simulated"):
        await diagnostics.run_diagnostic(directory, settings=settings, service=service)
    assert provider.calls == 2
    persisted = service.store.list("diagnostic_freeze")
    assert len(persisted) == 1
    assert not (directory / "judgment_freeze.json").exists()

    monkeypatch.setattr(diagnostics, "_write_once", original)
    resumed = await diagnostics.run_diagnostic(directory, settings=settings, service=service)
    assert resumed["status"] in {"frozen", "already_frozen"}
    assert provider.calls == 2
    assert json.loads((directory / "judgment_freeze.json").read_text()) == persisted[0]


@pytest.mark.asyncio
async def test_local_rate_capacity_wait_does_not_freeze_avoidable_unattempted_failures(
    setup_pilot, monkeypatch
):
    directory, settings, provider, service = setup_pilot(requests_per_minute=1)
    epoch = [time.time()]
    original_sleep = asyncio.sleep
    monkeypatch.setattr(time, "time", lambda: epoch[0])

    async def fast_clock_sleep(delay):
        epoch[0] += delay + 0.01
        await original_sleep(0)

    async def dormant_heartbeat(*_args, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(asyncio, "sleep", fast_clock_sleep)
    monkeypatch.setattr(service, "_heartbeat", dormant_heartbeat)
    await diagnostics.run_diagnostic(directory, settings=settings, service=service)
    freeze = json.loads((directory / "judgment_freeze.json").read_text())
    assert provider.calls == 2
    assert len(freeze["records"]) == 2
    assert all(record["judgment"]["error_category"] is None for record in freeze["records"])
