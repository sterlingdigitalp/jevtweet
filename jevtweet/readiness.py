"""Development-only collection planning, not a power or accuracy claim.

The evaluation module owns temporal membership and label eligibility. This module
only projects its development counts using the existing, frozen promotion policy.
It never retrieves final-test observations, opens a holdout, or fits a predictor.
"""

from __future__ import annotations

import math

from . import evaluation as ev
from .storage import Store


def _author_support(rows: list[dict]) -> dict:
    known = [r for r in rows if r.get("author_known", True) and r.get("author")]
    parents = {r["author"]: r["author"] for r in known}

    def root(author):
        while parents[author] != author:
            parents[author] = parents[parents[author]]
            author = parents[author]
        return author

    owner = {}
    for row, cluster in zip(known, ev._clusters(known)):
        previous = owner.setdefault(cluster, row["author"])
        parents[root(row["author"])] = root(previous)
    return {
        "known_authors": len(parents),
        "independent_author_clusters": len({root(a) for a in parents}),
        "positive_author_clusters": len({root(r["author"]) for r in known if r["label"]}),
        "unknown_author_rows": len(rows) - len(known),
    }


def _needed_rows(rate: float | None, policy: dict, fractions: dict, author_fraction: float) -> dict:
    if rate is None or not 0 < rate < 1:
        return {"eligible_rows": None, "constraints": {}}
    test_fraction = fractions["test"]
    requirements = {
        "minimum_eligible_rows": policy["minimum_eligible_rows"],
        "test_rows": math.ceil(policy["minimum_test_rows"] / test_fraction),
        "test_positives": math.ceil(policy["minimum_test_positives"] / (test_fraction * rate)),
        "test_negatives": math.ceil(policy["minimum_test_negatives"] / (test_fraction * (1 - rate))),
        "unfamiliar_author_rows": math.ceil(
            policy["minimum_author_holdout_rows"] / (test_fraction * author_fraction)
        ),
        "unfamiliar_author_positives": math.ceil(
            policy["minimum_author_holdout_positives"] / (test_fraction * author_fraction * rate)
        ),
        "unfamiliar_author_negatives": math.ceil(
            policy["minimum_author_holdout_negatives"] / (test_fraction * author_fraction * (1 - rate))
        ),
    }
    for name in ("train", "selection", "calibration"):
        fraction = fractions[name] * (1 - author_fraction)
        requirements[name] = math.ceil(
            max(
                ev.CONFIG["minimum_partition_rows"],
                ev.CONFIG["minimum_partition_positives"] / rate,
                ev.CONFIG["minimum_partition_negatives"] / (1 - rate),
            )
            / fraction
        )
    return {"eligible_rows": max(requirements.values()), "constraints": requirements}


def development_readiness(
    store: Store,
    *,
    synthetic: bool = False,
    task: str = "breakout_48h_v1",
    representative_sampling: bool = False,
    comparison_population: str = "",
    sampling_declaration: str = "",
    max_rows: int = 5000,
    cohort: dict | None = None,
) -> dict:
    data = ev.prepare_development(store, task=task, synthetic=synthetic, max_rows=max_rows, cohort=cohort)
    rows = data["rows"]
    count = len(rows)
    positives = sum(r["label"] for r in rows)
    rate = positives / count if count else None
    denominator = data.get("development_candidate_count", count)
    retention = count / denominator if denominator else None
    policy = ev.CONFIG["promotion_policy"]
    fractions = ev.CONFIG["split_fractions"]
    author_fraction = ev.CONFIG["author_holdout_fraction"]
    support = _author_support(rows)
    need = _needed_rows(rate, policy, fractions, author_fraction)
    eligible_need = need["eligible_rows"]
    raw_need = math.ceil(eligible_need / retention) if eligible_need and retention else None
    scenarios = []
    if rate is not None and 0 < rate < 1:
        for name, scenario_rate in (("half_observed_frequency", rate / 2), ("observed_frequency", rate)):
            estimate = _needed_rows(scenario_rate, policy, fractions, author_fraction)
            scenarios.append(
                {
                    "assumption": name,
                    "event_frequency": scenario_rate,
                    "eligible_rows": estimate["eligible_rows"],
                    "candidate_rows": math.ceil(estimate["eligible_rows"] / retention) if retention else None,
                }
            )
    cap = 5000
    return {
        "report_type": "development_collection_readiness",
        "policy_version": "readiness_v1",
        "status": "planning_estimate" if eligible_need else "insufficient_development_evidence",
        "scope": "development_only",
        "synthetic": synthetic,
        "task": task,
        "cohort": cohort or {},
        "declarations": {
            "representative_sampling": representative_sampling,
            "comparison_population": comparison_population,
            "sampling_declaration": sampling_declaration,
        },
        "test_outcomes_inspected": False,
        "holdout_consumed": False,
        "predictive_validation": "not_established",
        "observed": {
            "development_candidates": denominator,
            "eligible_rows": count,
            "positives": positives,
            "negatives": count - positives,
            "event_frequency": rate,
            "retention_rate": retention,
            "collection_counts": data.get("collection_counts", {}),
            "exclusion_counts": data.get("exclusion_counts", {}),
            "failure_coverage": data.get("failure_coverage", {}),
            **support,
        },
        "collection_estimates": {
            "eligible_rows_at_observed_frequency": eligible_need,
            "candidate_rows_at_observed_frequency": raw_need,
            "additional_candidates_at_observed_frequency": max(0, raw_need - denominator)
            if raw_need is not None
            else None,
            "binding_count_constraints": need["constraints"],
            "required_test_positive_outcomes": policy["minimum_test_positives"],
            "required_test_author_clusters": policy["minimum_test_author_clusters"],
            "required_unfamiliar_author_positive_outcomes": policy["minimum_author_holdout_positives"],
            "author_support_shortfall_vs_test_requirement": max(
                0, policy["minimum_test_author_clusters"] - support["independent_author_clusters"]
            ),
            "scenarios": scenarios,
        },
        "evaluation_cap": {
            "maximum_rows": cap,
            "requested_max_rows": max_rows,
            "nominal_test_fraction": fractions["test"],
            "expected_test_positives_at_cap": cap * fractions["test"] * rate if rate is not None else None,
            "expected_test_positives_after_observed_exclusions": cap * fractions["test"] * rate * retention
            if rate is not None and retention is not None
            else None,
            "minimum_test_event_frequency_at_cap": policy["minimum_test_positives"]
            / (cap * fractions["test"]),
            "minimum_unfamiliar_author_event_frequency_at_cap": policy["minimum_author_holdout_positives"]
            / (cap * fractions["test"] * author_fraction),
            "exceeds_cap_at_observed_frequency": raw_need > max_rows if raw_need is not None else None,
            "cap_comparison_basis": "Intended candidate rows including observed exclusions; nominal test-positive capacity above is before exclusions.",
            "action": "Do not rebalance, lower gates, pool repeated final tests, or inspect test outcomes to fit the cap. If collection needs exceed the cap, revise and version the resource policy before a new prospective evaluation, or retain editorial-only mode.",
        },
        "assumptions": {
            "split_fractions": fractions,
            "unfamiliar_author_fraction": author_fraction,
            "promotion_policy_version": policy["version"],
            "stable_development_event_frequency_and_retention": True,
            "event_frequency_same_for_unfamiliar_authors": True,
        },
        "limitations": [
            "Development frequency is descriptive; it is not a deployment probability or predictive validation.",
            "Count expectations are not a power calculation or a probability of passing the gates. Author concentration, dependence, temporal changes and purges can require more data.",
            "Unknown authors do not count as independent support. Observed development author clusters do not establish unseen final-test support; collect broad independent authors prospectively.",
            "Retention includes observed development exclusions; repeated posts from the same authors cannot repair independent-author shortfalls.",
            "Zero observed positives or negatives gives no finite two-class collection estimate. Collect more representative development data first.",
            "The 5,000-row bound applies to the intended prediction cohort before success and label exclusions; nominal test capacity is at most 1,000 rows before exclusions. At 1% frequency only about 10 test positives fit, below the required 40.",
            "Synthetic counts demonstrate planning mechanics only. This report neither reads final outcomes nor authorizes promotion.",
        ],
    }
