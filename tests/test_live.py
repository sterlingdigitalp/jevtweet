"""Opt-in private integration/robustness checks. Never enabled in public CI."""

import json
import os
from datetime import timedelta
from pathlib import Path

import pytest

from jevtweet.contracts import Candidate, EvidenceText, JudgeRequest, PredictionContext, Reference, now, uid
from jevtweet.service import Service
from jevtweet.settings import Settings

pytestmark = pytest.mark.skipif(
    os.getenv("JEVTWEET_RUN_LIVE") != "1",
    reason="Opt-in live check requires environment key and explicit spending ceiling",
)


async def test_live_private_robustness_and_persistence():
    settings = Settings(data_dir=Path(os.getenv("JEVTWEET_LIVE_TEST_DIR", "private/live-verification")))
    assert os.getenv("TYPESAFE_API_KEY") and settings.spend_limit_usd > 0
    service = Service(settings)
    cutoff = now()
    run_id = uid()
    texts = [
        "Our AI migration dropped an index. We now replay migrations on a production-shaped fixture and compare query plans before merging.",
        "We caught an index drop by replaying an AI-written migration on a production-shaped fixture. Query-plan comparisons now run before every merge.",
        "We do not replay AI-written migrations before merging. An index disappeared in production yesterday.",
        "This secret coding trick will change everything. Reply YES and follow before I reveal it.",
    ]
    requests = [
        JudgeRequest(
            candidate=Candidate(
                candidate_id=f"{run_id}-{i}", text=t, content_available_at=cutoff, synthetic=True
            ),
            context=PredictionContext(prediction_cutoff=cutoff),
            execution_mode="live",
        )
        for i, t in enumerate(texts)
    ]
    swap = requests[0].model_copy(deep=True)
    swap.audience_id = "humanoid_robotics"
    requests.append(swap)
    distract = requests[0].model_copy(deep=True)
    distract.context.topic = EvidenceText(
        text="A local bakery changed its opening hours this week.", occurred_at=cutoff, available_at=cutoff
    )
    requests.append(distract)
    refs = [
        Reference(
            candidate_id=f"{run_id}-ref-{i}",
            text=t,
            published_at=cutoff - timedelta(days=3),
            available_at=cutoff - timedelta(days=2),
        )
        for i, t in enumerate(
            [
                "A review checklist for generated SQL migrations should include constraints and rollback steps.",
                "We use snapshot fixtures to test schema changes before deploying database code.",
                "Our query planner regression suite identifies lost index usage during code review.",
            ]
        )
    ]
    for order in [refs, list(reversed(refs))]:
        enriched = requests[0].model_copy(deep=True)
        enriched.profile_id = "reference_enriched_v1"
        enriched.context.references = order
        requests.append(enriched)
    results = []
    for request in requests:
        result = await service.judge(request)
        results.append(result.model_dump(mode="json"))
        assert result.execution_mode == "live" and result.model_returned == settings.model
        assert result.status != "failed", result.error_category
        assert service.inspect(result.judgment_id)["state"]
    assert results[0]["score_1_to_5"] is not None
    artifact = {
        "run_id": run_id,
        "purpose": "Private sensitivity inspection; no invariance or predictive accuracy established.",
        "results": results,
        "sensitivity_pairs": [
            {
                "case": name,
                "continuous_difference": None
                if results[a]["score_continuous"] is None or results[b]["score_continuous"] is None
                else results[b]["score_continuous"] - results[a]["score_continuous"],
            }
            for name, a, b in [
                ("paraphrase", 0, 1),
                ("negation", 0, 2),
                ("bait", 0, 3),
                ("audience_swap", 0, 4),
                ("irrelevant_context", 0, 5),
                ("reference_order", 6, 7),
            ]
        ],
    }
    path = settings.data_dir / f"{run_id}.json"
    path.write_text(json.dumps(artifact, indent=2))
    path.chmod(0o600)
    assert service.account_store.spending()["charged_or_reserved_usd"] <= settings.spend_limit_usd
