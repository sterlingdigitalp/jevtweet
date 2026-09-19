"""Authoritative deterministic editorial calculation; never a breakout forecast."""
from __future__ import annotations

import math

from .contracts import Factor
from .rubric import load_rubric


def _available(factor: Factor | None, expected_type: str) -> bool:
    if factor is None or factor.type != expected_type or factor.error or factor.assessability != "assessable":
        return False
    value = {"score": factor.score, "choice": factor.choice, "noul": factor.noul}[expected_type]
    if expected_type == "choice":
        return isinstance(value, str) and bool(value)
    maximum = 4 if expected_type == "score" else 1
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(value) and 0 <= value <= maximum
    )


def score(
    factors: dict[str, Factor], profile_id: str, *, missing_evidence: list[str] | None = None
) -> dict:
    """Return the Judgment fields owned by editorial policy.

    No normalization over an incomplete subset, no confidence weighting, and no
    conversion of API failure into a low rating. Raw answers remain inspectable.
    """
    rubric = load_rubric()
    if profile_id not in rubric["profiles"]:
        raise ValueError(f"Unknown editorial profile: {profile_id}")
    policy = rubric["policy"]
    required = rubric["profiles"][profile_id]
    missing = list(dict.fromkeys(missing_evidence or []))
    unavailable = [key for key in required if not _available(factors.get(key), "score")]
    missing_checks = [
        key for key in rubric["required_checks"]
        if not _available(factors.get(key), rubric["questions"][key]["type"])
    ]
    flags = [f"missing_factor:{key}" for key in unavailable]
    flags += [f"unavailable_check:{key}" for key in missing_checks]
    flags += [f"missing_evidence:{key}" for key in missing]
    for key, factor in factors.items():
        if factor.confidence is not None and factor.confidence < policy["low_certainty"]:
            flags.append(f"low_answer_certainty:{key}")
        if factor.error and key not in unavailable and key not in missing_checks:
            flags.append(f"unavailable_answer:{key}")
    check = factors.get("assessability")
    assessable = _available(check, "choice") and check.choice == "assessable"
    if _available(check, "choice") and not assessable:
        flags.append(f"content_assessability:{check.choice}")
    missing_context = factors.get("missing_context")
    context_blocked = (
        _available(missing_context, "noul") and missing_context.noul >= policy["missing_context_threshold"]
    )
    if context_blocked:
        flags.append("missing_essential_context")
    instruction = factors.get("instruction_like")
    if _available(instruction, "noul") and instruction.noul >= policy["instruction_threshold"]:
        flags.append("instruction_like_content")
    if not _available(instruction, "noul"):
        flags.append("unavailable_check:instruction_like")
    # These are evidence absences, not judgments about quality. Nonessential
    # absences (e.g. history) are disclosed without changing the editorial score.
    blocked_evidence = [
        item for item in missing if item in {
            "empty_content", "empty_text", "essential_media", "missing_essential_media",
            "parent_context", "quoted_context", "missing_context", "essential_context",
        } or "essential" in item
    ]
    if profile_id == "reference_enriched_v1" and any("reference" in item for item in missing):
        blocked_evidence.extend(item for item in missing if "reference" in item)
    positives = [key for key in required if key != "aversion"]
    denominator = sum(policy["positive_weights"][key] for key in positives)
    contributions = []
    for key in positives:
        factor = factors.get(key)
        if _available(factor, "score"):
            raw = factor.score
            nearest = min(4, max(0, math.floor(raw + 0.5)))
            weight = policy["positive_weights"][key]
            contributions.append({
                "question_id": key,
                "label": rubric["questions"][key]["label"],
                "raw_score": raw,
                "normalized": raw / 4,
                "weight": weight,
                "quality_contribution": raw / 4 * weight / denominator,
                "nearest_criterion_index": nearest,
                "nearest_criterion": rubric["questions"][key]["criteria"][nearest],
            })
    ordered = sorted(contributions, key=lambda part: (-part["normalized"], part["question_id"]))
    valid_aversion = _available(factors.get("aversion"), "score")
    penalty = factors["aversion"].score / 4 * policy["aversion_penalty"] if valid_aversion else None
    quality = math.fsum(part["quality_contribution"] for part in contributions) if len(contributions) == len(positives) else None
    complete = not (unavailable or missing_checks or blocked_evidence or context_blocked) and assessable
    continuous = 1 + 4 * min(1, max(0, quality - penalty)) if complete else None
    integer = min(5, max(1, math.floor(continuous + 0.5))) if continuous is not None else None
    if complete:
        status = "scored"
    elif (check is not None and check.choice == "not_assessable") or any(
        item in {"empty_content", "empty_text"} for item in missing
    ):
        status = "abstained"
    elif not any(_available(factors.get(key), "score") for key in required):
        status = "failed"
    else:
        status = "partial"
    weak = sorted(contributions, key=lambda part: (part["normalized"], part["question_id"]))[:2]
    prompts = [rubric["review_prompts"][part["question_id"]] for part in weak]
    if penalty and penalty > 0:
        prompts.append(rubric["review_prompts"]["aversion"])
    if blocked_evidence or context_blocked:
        prompts.append("Supply the missing essential context or media description, then request a new judgment.")
    if "distinctiveness" in unavailable:
        prompts.append("Supply an adequate relevant reference set, or explicitly request a separate core-profile judgment.")
    if any(flag.startswith("low_answer_certainty:") for flag in flags):
        prompts.append("Inspect uncertain factor distributions; answer certainty does not measure forecast accuracy.")
    if "instruction_like_content" in flags:
        prompts.append("Review instruction-like source text; the detector does not guarantee injection safety.")
    explanation = {
        "meaning": "Editorial potential — not a calibrated probability.",
        "scope": "Explanation of this rubric calculation, not a causal explanation of future distribution.",
        "policy_version": policy["version"],
        "review_version": rubric["review_version"],
        "formula": (
            "quality = weighted_mean(raw_positive / 4); "
            f"adjusted = clamp(quality - {policy['aversion_penalty']} * raw_aversion / 4, 0, 1); "
            "editorial = 1 + 4 * adjusted; integer = floor(editorial + 0.5)"
        ),
        "contributions": contributions,
        "strongest_factors": ordered[:2],
        "weakest_factors": weak,
        "risk_penalty": penalty,
        "risk_penalty_rating_points": 4 * penalty if penalty is not None else None,
        "missing_factors": unavailable,
        "missing_checks": missing_checks,
        "missing_evidence": missing,
        "review_prompts": prompts,
        "direct_overall": {"role": "experimental_baseline", "included_in_composite": False},
    }
    return {
        "status": status,
        "score_continuous": continuous,
        "score_1_to_5": integer,
        "quality": quality,
        "risk_penalty": penalty,
        "review_flags": list(dict.fromkeys(flags)),
        "explanation": explanation,
        "factors": factors,
    }
