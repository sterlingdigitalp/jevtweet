import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from jevtweet.contracts import Candidate, JudgeRequest, PredictionContext
from jevtweet.provider import MockProvider, ProviderError
from jevtweet.service import Service
from jevtweet.settings import Settings
from jevtweet.storage import BudgetError, Store

T = datetime(2026, 1, 1, tzinfo=timezone.utc)


def request(**kw):
    return JudgeRequest(
        candidate=Candidate(
            candidate_id="test", text="A specific migration test", content_available_at=T, synthetic=True
        ),
        context=PredictionContext(prediction_cutoff=T),
        **kw,
    )


def settings(tmp_path, **kw):
    return Settings(data_dir=tmp_path / "data", account_dir=tmp_path / "account", **kw)


class CountingProvider(MockProvider):
    def __init__(self, errors=None):
        self.calls = 0
        self.errors = errors or []

    async def ask(self, state, questions):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        await asyncio.sleep(0.005)
        return await super().ask(state, questions)


async def test_coalesced_cache_and_invalidation(tmp_path):
    provider = CountingProvider()
    service = Service(settings(tmp_path), provider)
    a, b = await asyncio.gather(service.judge(request()), service.judge(request()))
    assert provider.calls == 1 and a.judgment_id == b.judgment_id
    assert (await service.judge(request())).cached
    swapped = request(audience_id="humanoid_robotics")
    c = await service.judge(swapped)
    assert c.input_hash != a.input_hash and provider.calls == 2
    run = service.inspect(a.judgment_id)
    assert run["state"] and run["questions"] and "diagnostics" not in run


async def test_retry_nonretry_partial_and_budget(tmp_path, monkeypatch):
    provider = CountingProvider([ProviderError("rate_limit", True, 0)])
    service = Service(settings(tmp_path), provider)
    result = await service.judge(request())
    assert result.status == "scored" and result.attempt_count == 2
    provider = CountingProvider([ProviderError("authentication")])
    s = Service(settings(tmp_path / "auth"), provider)
    result = await s.judge(request())
    assert result.status == "failed" and result.score_1_to_5 is None and provider.calls == 1
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline-test-placeholder")
    s = Service(settings(tmp_path / "budget", spend_limit_usd=0.00000001), CountingProvider())
    result = await s.judge(request(execution_mode="live"))
    assert (
        result.status == "failed"
        and result.error_category == "budget_or_configuration"
        and result.attempt_count == 0
    )


async def test_unknown_network_retains_reservations_across_data_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline-test-placeholder")
    config = settings(tmp_path, spend_limit_usd=1, max_attempts=2)
    provider = CountingProvider([ProviderError("connection", True, 0), ProviderError("connection", True, 0)])
    s = Service(config, provider)
    result = await s.judge(request(execution_mode="live"))
    assert result.status == "failed" and result.attempt_count == 2
    spent = s.account_store.spending()
    assert spent["charged_or_reserved_usd"] > 0 and spent["reserved_attempts"] == 2
    other = Service(Settings(data_dir=tmp_path / "other", account_dir=config.account_dir))
    assert other.account_store.spending() == spent


async def test_job_partial_and_crash_resume(tmp_path):
    provider = CountingProvider()
    s = Service(settings(tmp_path), provider)
    first = request()
    s.store.put("candidate", "test:1", first.candidate)
    s.store.put("context", "test:1", first.context)
    missing = first.candidate.model_copy(
        update={
            "candidate_id": "media",
            "media": __import__("jevtweet.contracts", fromlist=["Media"]).Media(kind="image", essential=True),
        }
    )
    s.store.put("candidate", "media:1", missing)
    s.store.put("context", "media:1", first.context)
    job = s.create_job(["test:1", "media:1"])
    job["items"][0]["status"] = "running"
    job["status"] = "running"
    s.store.put("job", job["job_id"], job, replace=True)
    done = await s.run_job(job["job_id"])
    assert done["status"] == "completed"
    assert done["items"][0]["status"] == "scored"
    assert done["items"][1]["status"] in ("partial", "abstained")
    assert s.store.get("judgment", done["items"][1]["judgment_id"])["score_1_to_5"] is None
    before = provider.calls
    await s.run_job(job["job_id"])
    assert provider.calls == before


async def test_cancellation_and_lease_release(tmp_path):
    class Slow(CountingProvider):
        async def ask(self, state, questions):
            self.calls += 1
            await asyncio.sleep(30)

    provider = Slow()
    s = Service(settings(tmp_path), provider)
    r = request()
    s.store.put("candidate", "test:1", r.candidate)
    s.store.put("context", "test:1", r.context)
    job = s.create_job(["test:1"])
    task = asyncio.create_task(s.run_job(job["job_id"]))
    for _ in range(100):
        if provider.calls:
            break
        await asyncio.sleep(0.005)
    s.cancel_job(job["job_id"])
    await task
    saved = s.store.get("job", job["job_id"])
    assert saved["status"] == "cancelled" and saved["items"][0]["status"] == "pending"
    s.provider = CountingProvider()
    assert (await s.run_job(job["job_id"]))["status"] == "completed"


def test_sqlite_contention_and_atomic_budget(tmp_path):
    store = Store(tmp_path)

    def write(i):
        store.put("x", str(i), {"value": i})
        try:
            store.reserve(str(i), 0.1, 1.000001, 120)
            return True
        except BudgetError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = list(pool.map(write, range(30)))
    assert len(store.list("x")) == 30 and sum(accepted) == 10
    assert store.spending()["charged_or_reserved_usd"] <= 1.000001
