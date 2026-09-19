"""Budgeted discovery adapter. No outcome or holdout information reaches Jev."""

import asyncio
import os

from .contracts import canonical, uid
from .storage import BudgetError


async def run_discovery(
    service, experiment_id, proposals, *, cost_limit_usd, max_rows=100, max_requests=100, live=False
):
    from .discovery import discover
    from .provider import ProviderError

    mode = "live" if live else "mock"
    provider = service._provider(mode)

    async def feature_provider(state, question):
        q = {k: v for k, v in question.items() if k in ("type", "instructions", "criteria")}
        qid = question["question_id"]
        questions = {qid: q}
        tokens = len(canonical({"state": state, "questions": questions}).encode()) + 1024
        if tokens > service.settings.max_request_tokens:
            return {
                "value": None,
                "estimated_cost_usd": 0,
                "execution_mode": mode,
                "status": "failed",
                "error": "request_limit",
            }
        estimate = tokens * service.settings.input_price_per_million / 1_000_000
        reservation = uid()
        slot = None
        if live:
            if not os.getenv("TYPESAFE_API_KEY"):
                raise BudgetError("TYPESAFE_API_KEY is required")
            service.account_store.reserve(
                reservation, estimate, service.settings.spend_limit_usd, service.settings.requests_per_minute
            )
        try:
            if live:
                slot = await service.acquire_account_slot(reservation)
            async with service._semaphore:
                async with asyncio.timeout(service.settings.timeout_seconds):
                    result = await provider.ask(state, questions)
            actual = result.usage.get("input_tokens")
            cost = estimate if live else 0
            if live and actual is not None:
                cost = actual * service.settings.input_price_per_million / 1_000_000
                service.account_store.reconcile(reservation, cost)
            f = result.factors.get(qid)
            value = (
                None
                if f is None or f.assessability != "assessable"
                else (f.score if f.type == "score" else f.noul)
            )
            return {
                "value": value,
                "estimated_cost_usd": cost,
                "execution_mode": mode,
                "status": "ok" if value is not None and result.error_category is None else "failed",
                "error": result.error_category,
            }
        except (ProviderError, TimeoutError) as exc:
            return {
                "value": None,
                "estimated_cost_usd": estimate if live else 0,
                "execution_mode": mode,
                "status": "failed",
                "error": getattr(exc, "category", "timeout"),
            }
        finally:
            if slot:
                service.account_store.release_lease(slot, reservation)

    return await discover(
        service.store,
        experiment_id,
        proposals,
        feature_provider=feature_provider,
        cost_limit_usd=cost_limit_usd,
        cost_estimate_per_request_usd=service.settings.max_request_tokens
        * service.settings.input_price_per_million
        / 1_000_000,
        max_rows=max_rows,
        max_requests=max_requests,
    )
