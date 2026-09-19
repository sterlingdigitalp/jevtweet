"""Independent synthetic runtime regressions; no provider network requests."""
import asyncio
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
import multiprocessing
import time

import pytest

from jevtweet.contracts import Candidate, JudgeRequest, OutcomeObservation, PredictionContext
from jevtweet.corpus import attach_outcome
from jevtweet.provider import MockProvider
from jevtweet.service import Service
from jevtweet.settings import Settings
from jevtweet.storage import BudgetError, Store

T = datetime(2025, 1, 1, tzinfo=timezone.utc)


def settings(tmp_path, **kwargs):
    return Settings(data_dir=tmp_path / "data", account_dir=tmp_path / "account", **kwargs)


def request(candidate_id="fixture"):
    return JudgeRequest(
        candidate=Candidate(candidate_id=candidate_id, text="A concrete regression fixture", content_available_at=T, synthetic=True),
        context=PredictionContext(prediction_cutoff=T),
    )


async def test_distinct_services_share_cache_without_duplicate_provider_call(tmp_path):
    class Counting(MockProvider):
        calls = 0

        async def ask(self, state, questions):
            self.calls += 1
            await asyncio.sleep(.05)
            return await super().ask(state, questions)

    provider = Counting()
    a, b = Service(settings(tmp_path), provider), Service(settings(tmp_path), provider)
    first, second = await asyncio.gather(a.judge(request()), b.judge(request("other-id")))
    assert provider.calls == 1
    assert first.input_hash == second.input_hash
    assert {first.candidate_id, second.candidate_id} == {"fixture", "other-id"}
    assert first.judgment_id != second.judgment_id
    assert len(a.store.list("judgment")) == 2
    assert len(a.store.list("run")) == 2


async def test_external_service_cancellation_survives_worker_save(tmp_path):
    started = asyncio.Event()
    released = asyncio.Event()

    class Waiting(MockProvider):
        async def ask(self, state, questions):
            started.set()
            await released.wait()
            return await super().ask(state, questions)

    worker = Service(settings(tmp_path), Waiting())
    controller = Service(settings(tmp_path), MockProvider())
    r = request()
    worker.store.put("candidate", "fixture:1", r.candidate)
    worker.store.put("context", "fixture:1", r.context)
    job = worker.create_job(["fixture:1"])
    task = asyncio.create_task(worker.run_job(job["job_id"]))
    try:
        await asyncio.wait_for(started.wait(), 2)
        controller.cancel_job(job["job_id"])
        # Allow the durable cancellation watcher to observe the request.
        await asyncio.sleep(.3)
        released.set()
        await asyncio.wait_for(task, 3)
        saved = controller.store.get("job", job["job_id"])
        assert saved["status"] == "cancelled"
        assert saved["items"][0]["status"] == "pending"
        worker.provider = MockProvider()
        resumed = await worker.run_job(job["job_id"])
        assert resumed["status"] == "completed"
        assert resumed["items"][0]["status"] == "scored"
    finally:
        released.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def test_concurrent_semantic_outcome_identity_is_immutable(tmp_path, monkeypatch):
    store = Store(tmp_path / "data")
    store.put("candidate", "fixture:1", Candidate(candidate_id="fixture", text="Synthetic fixture", published_at=T, content_available_at=T, synthetic=True))
    original_put = Store.put

    def delayed_put(self, kind, identity, value, **kwargs):
        # Widen any check-before-write race without imposing a transaction model.
        if kind == "outcome":
            time.sleep(.03)
        return original_put(self, kind, identity, value, **kwargs)

    monkeypatch.setattr(Store, "put", delayed_put)

    def attach(i):
        outcome = OutcomeObservation(
            observation_id=f"observation-{i}", candidate_id="fixture", source="synthetic_manual",
            observed_at=T + timedelta(hours=48), available_at=T + timedelta(hours=48),
            elapsed_hours=48, views=i, synthetic=True,
        )
        try:
            attach_outcome(store, outcome)
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        accepted = list(pool.map(attach, range(12)))
    assert sum(accepted) == 1
    assert len(store.list("outcome")) == 1


def _reserve_process(args):
    directory, index = args
    store = Store(directory)
    try:
        store.reserve(f"process-{index}", .25, 1.0, 120)
        return True
    except BudgetError:
        return False


def test_spending_ceiling_survives_independent_processes(tmp_path):
    directory = str(tmp_path / "account")
    Store(directory)
    with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context("fork")) as pool:
        accepted = list(pool.map(_reserve_process, [(directory, i) for i in range(12)]))
    assert sum(accepted) == 4
    assert Store(directory).spending() == {"charged_or_reserved_usd": 1.0, "reserved_attempts": 4}
    with pytest.raises(BudgetError):
        Store(directory).reserve("after-restart", .01, 1.0, 120)


async def test_job_and_judgment_leases_refresh_during_slow_provider(tmp_path):
    """Provider work must not leave the job/judgment leases unrefreshed."""
    started = asyncio.Event()
    released = asyncio.Event()

    class Waiting(MockProvider):
        async def ask(self, state, questions):
            started.set()
            await released.wait()
            return await super().ask(state, questions)

    service = Service(settings(tmp_path), Waiting())
    r = request()
    service.store.put("candidate", "fixture:1", r.candidate)
    service.store.put("context", "fixture:1", r.context)
    job = service.create_job(["fixture:1"])
    task = asyncio.create_task(service.run_job(job["job_id"]))
    try:
        await asyncio.wait_for(started.wait(), 2)
        with service.store.connect() as db:
            before = {row["key"]: row["expires_epoch"] for row in db.execute("SELECT * FROM leases")}
        assert any(key.startswith("judgment:") for key in before)
        assert f'job:{job["job_id"]}' in before
        await asyncio.sleep(5.2)
        with service.store.connect() as db:
            after = {row["key"]: row["expires_epoch"] for row in db.execute("SELECT * FROM leases")}
        assert all(after[key] > expiry for key, expiry in before.items())
        released.set()
        assert (await asyncio.wait_for(task, 2))["status"] == "completed"
        with service.store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM leases").fetchone()[0] == 0
    finally:
        released.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def test_default_account_ledger_is_independent_of_working_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("JEVTWEET_ACCOUNT_DIR", raising=False)
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.chdir(first)
    first_ledger = Settings().account_dir.resolve()
    monkeypatch.chdir(second)
    second_ledger = Settings().account_dir.resolve()
    assert first_ledger == second_ledger
