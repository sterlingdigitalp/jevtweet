"""One application service for API, CLI, and corpus jobs."""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path

from .contracts import (
    Candidate,
    JudgeRequest,
    Judgment,
    PredictionContext,
    ProviderResult,
    canonical,
    digest,
    now,
    uid,
)
from .corpus import candidate_key
from .request_budget import request_budget
from .settings import Settings
from .state import audience_by_id, build_state
from .storage import BudgetError, Store


def code_commit():
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent, stderr=subprocess.DEVNULL, text=True
        ).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=Path(__file__).parent, stderr=subprocess.DEVNULL, text=True
        ).strip()
        return commit + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unversioned"


class Service:
    def __init__(self, settings: Settings | None = None, provider=None):
        self.settings = settings or Settings()
        self.store = Store(self.settings.data_dir)
        self.store.restriction_registry_dir = self.settings.account_dir
        self.account_store = Store(self.settings.account_dir)
        self.provider = provider
        self._semaphore = asyncio.Semaphore(self.settings.concurrency)
        self._inflight = {}
        self._tasks = {}
        self._cancel = {}

    async def acquire_account_slot(self, owner):
        while True:
            for i in range(self.settings.concurrency):
                key = f"provider-slot:{i}"
                if self.account_store.acquire_lease(key, owner, self.settings.timeout_seconds + 90):
                    return key
            await asyncio.sleep(0.1)

    async def _heartbeat(self, store, key, owner, seconds=300):
        while True:
            await asyncio.sleep(min(5, seconds / 3))
            store.refresh_lease(key, owner, seconds)

    def _provider(self, mode):
        from .provider import MockProvider, TypeSafeProvider

        return self.provider or (MockProvider() if mode == "mock" else TypeSafeProvider(self.settings))

    def _prepare(self, request):
        from .rubric import build_questions, load_rubric

        audience = audience_by_id(request.audience_id)
        state = build_state(request.candidate, request.context, audience)
        questions = build_questions(state, request.profile_id)
        rubric = load_rubric()
        fingerprint = digest(
            {
                "state": state,
                "questions": questions,
                "rubric": rubric,
                "model": self.settings.model,
                "sdk": "0.7.0",
                "profile": request.profile_id,
                "mode": request.execution_mode,
            }
        )
        return audience, state, questions, rubric, fingerprint

    async def judge(self, request: JudgeRequest) -> Judgment:
        audience, state, questions, rubric, fingerprint = self._prepare(request)
        self.store.put("candidate", candidate_key(request.candidate), request.candidate)
        # A candidate can be assessed in several contexts; full snapshots live in runs.
        if not self.store.get("context", candidate_key(request.candidate)):
            self.store.put("context", candidate_key(request.candidate), request.context)
        cached = self.store.cached(fingerprint)
        if cached:
            return self._reuse(cached, request, state, questions)
        if fingerprint in self._inflight:
            result = await asyncio.shield(self._inflight[fingerprint])
            return self._reuse(result.model_dump(mode="json"), request, state, questions)
        task = asyncio.create_task(self._execute(request, audience, state, questions, rubric, fingerprint))
        self._inflight[fingerprint] = task
        try:
            return await task
        finally:
            self._inflight.pop(fingerprint, None)

    def _reuse(self, cached, request, state, questions):
        result = Judgment.model_validate(cached)
        same = (result.candidate_id, result.candidate_version) == (
            request.candidate.candidate_id,
            request.candidate.candidate_version,
        )
        result.cached = True
        if not same:
            original = result.judgment_id
            result.judgment_id = uid()
            result.candidate_id = request.candidate.candidate_id
            result.candidate_version = request.candidate.candidate_version
            result.provenance = {**result.provenance, "reused_from_judgment_id": original}
            self._persist(result, request, state, questions, {})
        return result

    async def _execute(self, request, audience, state, questions, rubric, fingerprint):
        from .provider import ProviderError
        from .scoring import score

        start = time.monotonic()
        owner = uid()
        lease = "judgment:" + fingerprint
        while not self.store.acquire_lease(lease, owner):
            cached = self.store.cached(fingerprint)
            if cached:
                return self._reuse(cached, request, state, questions)
            await asyncio.sleep(0.2)
        heartbeat = asyncio.create_task(self._heartbeat(self.store, lease, owner))
        try:
            cached = self.store.cached(fingerprint)
            if cached:
                return self._reuse(cached, request, state, questions)
            attempts = 0
            total_cost = 0.0
            response = ProviderResult()
            error = None
            diagnostics = {}
            # UTF-8 bytes + overhead is a deliberately conservative tokenizer-independent ceiling.
            budget = request_budget(state, questions, self.settings)
            if not budget["within_limits"]:
                error = "request_limit"
            elif not state["candidate"]["text"].strip():
                error = "empty_content"
            if not error:
                async with self._semaphore:
                    provider = self._provider(request.execution_mode)
                    for attempt in range(self.settings.max_attempts):
                        reservation = uid()
                        slot = None
                        try:
                            if request.execution_mode == "live":
                                if not os.getenv("TYPESAFE_API_KEY"):
                                    raise BudgetError("TYPESAFE_API_KEY is not configured")
                                slot = await self.acquire_account_slot(reservation)
                                estimate = budget["per_attempt_usd"]
                                self.account_store.reserve(
                                    reservation,
                                    estimate,
                                    self.settings.spend_limit_usd,
                                    self.settings.requests_per_minute,
                                )
                                total_cost += estimate
                            attempts += 1
                            async with asyncio.timeout(self.settings.timeout_seconds):
                                response = await provider.ask(state, questions)
                            if request.execution_mode == "live":
                                actual_tokens = response.usage.get("input_tokens")
                                if isinstance(actual_tokens, int) and actual_tokens >= 0:
                                    actual = actual_tokens * self.settings.input_price_per_million / 1_000_000
                                    self.account_store.reconcile(reservation, actual)
                                    total_cost += actual - estimate
                            error = response.error_category
                            diagnostics = response.diagnostics
                            break
                        except BudgetError as exc:
                            error = "budget_or_configuration"
                            diagnostics = {"message": str(exc)}
                            break
                        except (ProviderError, TimeoutError) as exc:
                            error = getattr(exc, "category", "timeout")
                            diagnostics = {"category": error}
                            retryable = isinstance(exc, TimeoutError) or getattr(exc, "retryable", False)
                            delay = getattr(exc, "retry_after", None)
                            if not retryable or attempt + 1 >= self.settings.max_attempts:
                                break
                            if delay is not None and delay > 60:
                                diagnostics["retry_after_seconds"] = delay
                                break
                            await asyncio.sleep(
                                max(0, float(delay)) if delay is not None else min(0.5 * 2**attempt, 8)
                            )
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            # No provider exception text: some SDK errors may echo payloads/headers.
                            error = "internal_provider_error"
                            diagnostics = {"exception_type": type(exc).__name__}
                            break
                        finally:
                            if slot:
                                self.account_store.release_lease(slot, reservation)
            factors = response.factors
            result_fields = score(factors, request.profile_id, missing_evidence=state["evidence"]["missing"])
            if error and not factors:
                result_fields.update(
                    status="abstained" if error == "empty_content" else "failed",
                    score_continuous=None,
                    score_1_to_5=None,
                    quality=None,
                    risk_penalty=None,
                )
            factors = result_fields["factors"]
            result_fields["evidence_completeness"] = (
                "partial"
                if state["evidence"]["missing"]
                or result_fields["explanation"].get("missing_factors")
                or result_fields["explanation"].get("missing_checks")
                else "complete"
            )
            result_fields["execution_status"] = (
                "not_attempted"
                if not attempts
                else (
                    "partial" if any(f.assessability == "assessable" for f in factors.values()) else "failed"
                )
                if error
                else "succeeded"
            )
            if diagnostics.get("numerical_precision_notes"):
                result_fields["review_flags"].append("provider_display_rounding_validated")
            result = Judgment(
                **result_fields,
                candidate_id=request.candidate.candidate_id,
                candidate_version=request.candidate.candidate_version,
                execution_mode=request.execution_mode,
                profile_id=request.profile_id,
                audience_id=audience.audience_id,
                audience_version=audience.version,
                rubric_version=rubric["version"],
                model_requested=self.settings.model,
                model_returned=response.model_returned,
                input_hash=fingerprint,
                reference_set_hash=digest(state["references"]),
                code_commit=code_commit(),
                completed_at=now(),
                usage=response.usage,
                estimated_cost=total_cost,
                latency=time.monotonic() - start,
                attempt_count=attempts,
                error_category=error,
                provenance={
                    "provider": "deterministic_mock_v1"
                    if request.execution_mode == "mock"
                    else "typesafe_sdk",
                    "reference_rule": request.context.reference_rule,
                    "preprocessing_version": "allowlist_v1",
                    "rubric_hash": digest(rubric),
                    "response_validation_version": rubric["validation"]["version"],
                    "price_as_of": self.settings.price_as_of,
                    "synthetic": request.candidate.synthetic,
                },
            )
            self._persist(result, request, state, questions, diagnostics)
            return result
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            self.store.release_lease(lease, owner)

    def _persist(self, result, request, state, questions, diagnostics):
        run = {
            "judgment_id": result.judgment_id,
            "request": request.model_dump(mode="json"),
            "state": state,
            "questions": questions,
            "diagnostics": diagnostics,
        }
        with self.store.transaction() as db:
            for kind, body in (("judgment", result.model_dump(mode="json")), ("run", run)):
                db.execute(
                    "INSERT INTO records(kind,id,body,created_at) VALUES(?,?,?,?)",
                    (kind, result.judgment_id, canonical(body), now().isoformat()),
                )
            if result.status in ("scored", "partial", "abstained") and not result.error_category:
                db.execute("INSERT OR IGNORE INTO cache VALUES(?,?)", (result.input_hash, result.judgment_id))

    def inspect(self, judgment_id):
        judgment = self.store.get("judgment", judgment_id)
        if judgment is None:
            raise ValueError("Unknown judgment")
        run = self.store.get("run", judgment_id) or {}
        return {"judgment": judgment, **{k: v for k, v in run.items() if k != "diagnostics"}}

    async def compare(self, requests):
        if not 2 <= len(requests) <= 8:
            raise ValueError("Compare requires 2–8 variants")
        results = await asyncio.gather(*(self.judge(r) for r in requests))
        groups = {
            (
                r.profile_id,
                r.audience_id,
                r.audience_version,
                r.rubric_version,
                r.execution_mode,
                r.reference_set_hash,
            )
            for r in results
        }
        return {
            "results": [r.model_dump(mode="json") for r in results],
            "comparable": len(groups) == 1,
            "limitations": ["Different comparison context or profile cannot establish a shared rank."]
            if len(groups) > 1
            else ["Rubric comparison is not evidence of a causal improvement in reach."],
        }

    def create_job(
        self,
        candidate_ids,
        audience_id="production_ai_coding",
        profile_id="text_core_v1",
        execution_mode="mock",
    ):
        if not 1 <= len(candidate_ids) <= self.settings.max_rows:
            raise ValueError("Job row cap exceeded or empty selection")
        audience_by_id(audience_id)
        if profile_id not in ("text_core_v1", "reference_enriched_v1") or execution_mode not in (
            "mock",
            "live",
        ):
            raise ValueError("Invalid profile or execution mode")
        for k in candidate_ids:
            if self.store.get("candidate", k) is None:
                raise ValueError("Unknown candidate/version " + k)
        job = {
            "job_id": uid(),
            "status": "pending",
            "audience_id": audience_id,
            "profile_id": profile_id,
            "execution_mode": execution_mode,
            "created_at": now().isoformat(),
            "items": [
                {"candidate_key": k, "status": "pending", "judgment_id": None, "error": None}
                for k in dict.fromkeys(candidate_ids)
            ],
        }
        self.store.put("job", job["job_id"], job)
        return job

    async def run_job(self, job_id):
        owner = uid()
        lease = "job:" + job_id
        if not self.store.acquire_lease(lease, owner, 180):
            raise ValueError("Job is already running; wait for its lease to expire after a crash")
        job = self.store.get("job", job_id)
        if job is None:
            self.store.release_lease(lease, owner)
            raise ValueError("Unknown job")
        cancel = self._cancel[job_id] = asyncio.Event()
        self.store.put("job_control", job_id, {"cancel_requested": False}, replace=True)
        job["status"] = "running"
        job["stop_reason"] = None
        self.store.put("job", job_id, job, replace=True)
        lock = asyncio.Lock()
        heartbeat = asyncio.create_task(self._heartbeat(self.store, lease, owner, 180))

        async def watch_cancel():
            while True:
                control = self.store.get("job_control", job_id) or {}
                if control.get("cancel_requested"):
                    cancel.set()
                    for task in self._tasks.get(job_id, []):
                        task.cancel()
                    return
                await asyncio.sleep(0.1)

        watcher = asyncio.create_task(watch_cancel())

        async def save():
            async with lock:
                self.store.refresh_lease(lease, owner, 180)
                self.store.put("job", job_id, job, replace=True)

        async def one(item):
            if item["status"] in ("scored", "partial", "abstained") and not item.get("error"):
                return
            if cancel.is_set():
                return
            c = Candidate.model_validate(self.store.get("candidate", item["candidate_key"]))
            ctx = PredictionContext.model_validate(
                self.store.get("context", item["candidate_key"])
                or {"prediction_cutoff": c.content_available_at}
            )
            # References are explicit: batch does not automatically draw from unknown evaluation splits.
            request = JudgeRequest(
                candidate=c,
                context=ctx,
                audience_id=job["audience_id"],
                profile_id=job["profile_id"],
                execution_mode=job["execution_mode"],
            )
            item.update(status="running", error=None)
            await save()
            try:
                result = await self.judge(request)
                item.update(status=result.status, judgment_id=result.judgment_id, error=result.error_category)
                if result.error_category == "budget_or_configuration":
                    job["stop_reason"] = "budget_or_configuration"
                    cancel.set()
            except asyncio.CancelledError:
                item.update(status="pending", error="cancelled")
                await save()
                raise
            except Exception as exc:
                item.update(status="failed", error=type(exc).__name__)
            await save()

        try:
            # At most concurrency tasks exist, so cancelling does not leave a large queued fan-out.
            for offset in range(0, len(job["items"]), self.settings.concurrency):
                if cancel.is_set():
                    break
                tasks = [
                    asyncio.create_task(one(item))
                    for item in job["items"][offset : offset + self.settings.concurrency]
                ]
                self._tasks[job_id] = tasks
                await asyncio.gather(*tasks)
            job["status"] = (
                "budget_stopped"
                if job.get("stop_reason") == "budget_or_configuration"
                else "cancelled"
                if cancel.is_set()
                else (
                    "completed_with_errors"
                    if any(i["status"] == "failed" or i.get("error") for i in job["items"])
                    else "completed"
                )
            )
        except asyncio.CancelledError:
            job["status"] = "cancelled"
        finally:
            watcher.cancel()
            heartbeat.cancel()
            await asyncio.gather(watcher, heartbeat, return_exceptions=True)
            await save()
            self.store.release_lease(lease, owner)
            self._tasks.pop(job_id, None)
            self._cancel.pop(job_id, None)
        return job

    def cancel_job(self, job_id):
        job = self.store.get("job", job_id)
        if not job:
            raise ValueError("Unknown job")
        self.store.put(
            "job_control", job_id, {"cancel_requested": True, "requested_at": now().isoformat()}, replace=True
        )
        event = self._cancel.get(job_id)
        if event:
            event.set()
        for task in self._tasks.get(job_id, []):
            task.cancel()
        job["status"] = "cancelled"
        self.store.put("job", job_id, job, replace=True)
        return job
