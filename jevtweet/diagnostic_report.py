"""Private, post-freeze descriptive diagnostics, never predictive evaluation.

This reader has no provider, training, promotion, or holdout access. It validates
all frozen execution bindings before opening metric snapshots. Its only numeric
comparisons use the unchanged continuous editorial score and the independent
direct-overall baseline. Source snapshots do not become timed outcome labels.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter
from datetime import datetime
from pathlib import Path

from .contracts import Audience, JudgeRequest, Judgment, canonical, digest, now
from .diagnostic_contracts import DiagnosticAuthorization, DiagnosticSourceRecord, MetricSnapshot
from .rubric import build_questions, load_rubric
from .state import build_state

_ALTERNATIVES = [
    "Missing conversational, cultural, temporal, or media context may affect the judgment.",
    "The fixed seed audience may differ from the author's actual audience.",
    "Editorial potential and distribution or engagement are different constructs.",
    "Unequal exposure, post age, unknown sampling, and chance may explain the difference.",
]
_LIMITATIONS = [
    "Development-only diagnostic corpus; permanently ineligible for calibration, final test, and promotion.",
    "Observed source metrics are untimed snapshots, not known 48-hour outcomes or breakout labels.",
    "Publication times, source timezone, observation times, and elapsed windows are unknown.",
    "Author history, distribution mechanisms, and representative sampling are not established.",
    "Associations describe this selected corpus; they do not establish predictive accuracy or calibration.",
    "Answer certainty is not forecast probability, evidence completeness, or execution success.",
    "A rank disagreement does not identify a causal explanation or prove a defect in either construct.",
]


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Required valid {path.name} is unavailable") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _jsonl(path: Path) -> list[dict]:
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError) as exc:
        raise ValueError(f"Required valid {path.name} is unavailable") from exc


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ValueError(f"Required {path.name} is unavailable") from exc


def _ids(value, name: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError(f"{name} must contain unique candidate identifiers")
    return value


def _verify_freeze(directory: Path) -> tuple[dict, dict, dict, dict[str, Judgment]]:
    # Open the execution freeze first. In particular, do not read snapshots or
    # normalized source rows as a convenience before this entire function passes.
    freeze = _json(directory / "judgment_freeze.json")
    if freeze.get("freeze_hash") != digest({k: v for k, v in freeze.items() if k != "freeze_hash"}):
        raise ValueError("Judgment freeze digest does not match")
    if freeze.get("execution_mode") != "live":
        raise ValueError("A diagnostic report requires frozen live judgments, never mock outputs")
    try:
        frozen_at = datetime.fromisoformat(freeze["frozen_at"])
        if frozen_at.tzinfo is None:
            raise ValueError("Missing timezone")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Judgment freeze timestamp must include its timezone") from exc
    protocol = _json(directory / "protocol.json")
    if protocol.get("protocol_hash") != digest({k: v for k, v in protocol.items() if k != "protocol_hash"}):
        raise ValueError("Protocol digest does not match")
    if protocol.get("protocol_version") != "diagnostic_v1":
        raise ValueError("Unsupported diagnostic protocol")
    if freeze.get("protocol_hash") != protocol["protocol_hash"]:
        raise ValueError("Judgment freeze belongs to a different protocol")
    if protocol.get("requests_file") != "diagnostic_requests.jsonl":
        raise ValueError("Protocol must bind the diagnostic_requests.jsonl artifact")
    for name, expected in (
        ("source_manifest.json", protocol.get("source_manifest_sha256")),
        ("diagnostic_requests.jsonl", protocol.get("requests_sha256")),
        ("prepared_inputs.jsonl", protocol.get("prepared_inputs_sha256")),
    ):
        if _sha(directory / name) != expected:
            raise ValueError(f"Frozen protocol binding changed: {name}")
    authorization = _json(directory / "authorization.json")
    approved = DiagnosticAuthorization.model_validate(authorization)
    if digest(authorization) != freeze.get("authorization_hash"):
        raise ValueError("Authorization binding changed")
    if approved.protocol_hash != protocol["protocol_hash"]:
        raise ValueError("Authorization belongs to a different protocol")
    manifest = _json(directory / "source_manifest.json")
    candidate_ids = _ids(protocol.get("candidate_ids"), "Protocol cohort")
    if not candidate_ids:
        raise ValueError("Protocol initial cohort cannot be empty")
    all_ids = _ids(manifest.get("candidate_ids"), "Source manifest cohort")
    eligible = _ids(manifest.get("eligible_candidate_ids"), "Source manifest initial cohort")
    if manifest.get("record_count") != len(all_ids) or set(candidate_ids) != set(eligible):
        raise ValueError("Protocol and source manifest cohort disagree")
    if not set(candidate_ids).issubset(all_ids):
        raise ValueError("Protocol includes unknown source candidates")
    unexecuted = _ids(freeze.get("unexecuted_candidate_ids"), "Unexecuted cohort")
    records = freeze.get("records")
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        raise ValueError("Judgment freeze records are invalid")
    executed_ids = _ids([record.get("candidate_id") for record in records], "Executed cohort")
    if (
        set(executed_ids) & set(unexecuted)
        or set(executed_ids) | set(unexecuted) != set(candidate_ids)
        or executed_ids != [cid for cid in candidate_ids if cid not in unexecuted]
        or unexecuted != [cid for cid in candidate_ids if cid not in executed_ids]
    ):
        raise ValueError("Frozen records must preserve exact protocol cohort membership and order")
    audience = protocol.get("audience", {})
    if not isinstance(audience, dict):
        raise ValueError("Protocol audience is invalid")
    required = ["model", "profile_id", "rubric_version", "rubric_hash", "code_commit", "sdk_version"]
    if any(not isinstance(protocol.get(key), str) or not protocol[key] for key in required):
        raise ValueError("Protocol configuration is incomplete")
    if not audience.get("audience_id") or not audience.get("version"):
        raise ValueError("Protocol audience configuration is incomplete")
    prepared_audience = Audience.model_validate(audience.get("snapshot"))
    if (
        digest(prepared_audience) != audience.get("hash")
        or prepared_audience.audience_id != audience["audience_id"]
        or prepared_audience.version != audience["version"]
    ):
        raise ValueError("Prepared audience snapshot differs from the protocol")
    rubric = load_rubric()
    if digest(rubric) != protocol["rubric_hash"] or rubric["version"] != protocol["rubric_version"]:
        raise ValueError("Report requires the unchanged rubric bound to the protocol")
    requests = [JudgeRequest.model_validate(row) for row in _jsonl(directory / "diagnostic_requests.jsonl")]
    prepared = _jsonl(directory / "prepared_inputs.jsonl")
    if (
        [request.candidate.candidate_id for request in requests] != candidate_ids
        or any(not isinstance(item, dict) for item in prepared)
        or [item.get("candidate_id") for item in prepared] != candidate_ids
    ):
        raise ValueError("Prepared request/input membership and order differ from the protocol")
    inputs_by_id, requests_by_id = {}, {}
    for request, item in zip(requests, prepared, strict=True):
        if (
            request.execution_mode != "live"
            or request.profile_id != protocol["profile_id"]
            or request.audience_id != audience["audience_id"]
            or request.context.evaluation_split != "development"
        ):
            raise ValueError("Prepared request configuration differs from the protocol")
        state = build_state(request.candidate, request.context, prepared_audience)
        questions = build_questions(state, request.profile_id)
        fingerprint = digest(
            {
                "state": state,
                "questions": questions,
                "rubric": rubric,
                "model": protocol["model"],
                "sdk": protocol["sdk_version"],
                "profile": request.profile_id,
                "mode": request.execution_mode,
            }
        )
        if item != {
            "candidate_id": request.candidate.candidate_id,
            "state": state,
            "questions": questions,
            "input_hash": fingerprint,
        }:
            raise ValueError("Prepared input fingerprint, state, or questions changed")
        inputs_by_id[request.candidate.candidate_id] = item
        requests_by_id[request.candidate.candidate_id] = request
    judgments = {}
    for record in records:
        judgment = Judgment.model_validate(record.get("judgment"))
        if judgment.candidate_id != record["candidate_id"]:
            raise ValueError("Frozen judgment identity does not match its record")
        expected = {
            "execution_mode": "live",
            "mode": "editorial",
            "model_requested": protocol["model"],
            "profile_id": protocol["profile_id"],
            "audience_id": audience["audience_id"],
            "audience_version": audience["version"],
            "rubric_version": protocol["rubric_version"],
            "code_commit": protocol["code_commit"],
            "sdk_version": protocol["sdk_version"],
            "input_hash": inputs_by_id[judgment.candidate_id]["input_hash"],
            "candidate_version": requests_by_id[judgment.candidate_id].candidate.candidate_version,
        }
        if any(getattr(judgment, field) != value for field, value in expected.items()):
            raise ValueError("Frozen judgment configuration does not match its approved protocol")
        if judgment.provenance.get("provider") != "typesafe_sdk":
            raise ValueError("Frozen judgment provider must be typesafe_sdk, never a mock provider")
        if judgment.provenance.get("rubric_hash") != protocol["rubric_hash"]:
            raise ValueError("Frozen judgment rubric digest changed")
        operational_failure = (
            judgment.status == "failed"
            and judgment.execution_status in {"failed", "not_attempted"}
            and judgment.error_category is not None
            and not judgment.factors
            and judgment.score_continuous is None
            and judgment.score_1_to_5 is None
            and judgment.model_returned is None
        )
        if not operational_failure and judgment.model_returned != protocol["model"]:
            raise ValueError("Frozen judgment did not return the approved model")
        if judgment.breakout_probability is not None or judgment.predictor_id is not None:
            raise ValueError("Diagnostic first judgments must not contain a predictor forecast")
        judgments[judgment.candidate_id] = judgment
    return protocol, freeze, manifest, judgments


def _ranks(values: list[float]) -> list[float]:
    """One-based average ranks, with exact ties and stable ordering."""
    indices = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indices):
        j = i + 1
        while j < len(indices) and values[indices[j]] == values[indices[i]]:
            j += 1
        for index in indices[i:j]:
            ranks[index] = (i + 1 + j) / 2
        i = j
    return ranks


def _ties(values: list[float]) -> dict:
    sizes = [count for count in Counter(values).values() if count > 1]
    return {"tie_groups": len(sizes), "tied_records": sum(sizes), "excess_ties": sum(n - 1 for n in sizes)}


def _association(rows: list[dict], x: str, y: str) -> dict:
    pairs = [(row[x], row[y]) for row in rows if row[x] is not None and row[y] is not None]
    xs, ys = ([pair[i] for pair in pairs] for i in (0, 1))
    reasons = []
    if len(pairs) < 3:
        reasons.append("fewer_than_three_pairs")
    if pairs and len(set(xs)) < 2:
        reasons.append("constant_x")
    if pairs and len(set(ys)) < 2:
        reasons.append("constant_y")
    coefficient = None
    if not reasons:
        rank_x, rank_y = _ranks(xs), _ranks(ys)
        mean_x, mean_y = sum(rank_x) / len(pairs), sum(rank_y) / len(pairs)
        covariance = sum((a - mean_x) * (b - mean_y) for a, b in zip(rank_x, rank_y, strict=True))
        variance_x = sum((a - mean_x) ** 2 for a in rank_x)
        variance_y = sum((b - mean_y) ** 2 for b in rank_y)
        coefficient = max(-1.0, min(1.0, covariance / math.sqrt(variance_x * variance_y)))
    return {
        "method": "descriptive_spearman_average_ranks_v1",
        "subset": "fully_scored_first_judgments_only",
        "n": len(pairs),
        "subset_size": len(rows),
        "missing_pairs": len(rows) - len(pairs),
        "missing_x": sum(row[x] is None for row in rows),
        "missing_y": sum(row[y] is None for row in rows),
        "x_ties": _ties(xs),
        "y_ties": _ties(ys),
        "spearman": coefficient,
        "undefined_reasons": reasons,
        "interpretation": "Selected-corpus description; no predictive or causal inference.",
    }


def _disagreements(rows: list[dict]) -> dict:
    pairs = [row for row in rows if row["composite"] is not None and row["views"] is not None]
    n = len(pairs)
    rank_x, rank_y = _ranks([row["composite"] for row in pairs]), _ranks([row["views"] for row in pairs])
    cases = []
    for row, x, y in zip(pairs, rank_x, rank_y, strict=True):
        xp, yp = ((x - 1) / (n - 1), (y - 1) / (n - 1)) if n > 1 else (0.5, 0.5)
        cases.append(
            {
                "candidate_id": row["candidate_id"],
                "composite": row["composite"],
                "views": row["views"],
                "composite_percentile": xp,
                "views_percentile": yp,
                "absolute_percentile_gap": abs(xp - yp),
                "direction": "editorial_above_views"
                if xp > yp
                else "views_above_editorial"
                if yp > xp
                else "tied_ranks",
                "alternative_explanations": _ALTERNATIVES,
                "case_interpretation": "Unreviewed contrast; no semantic or causal explanation established.",
            }
        )
    ranked = sorted(cases, key=lambda row: (-row["absolute_percentile_gap"], row["candidate_id"]))
    extremes = []
    if n >= 6:
        for axis in ("composite", "views"):
            ordered = sorted(cases, key=lambda row: (row[axis], row["candidate_id"]))
            extremes.append({"axis": axis, "low": ordered[0], "high": ordered[-1]})
    return {
        "rule_version": "rank_gap_v1",
        "rule": (
            "At least three complete scored pairs: top three absolute differences between "
            "average-rank percentiles (rank-1)/(n-1), breaking ties by candidate_id. "
            "At least six pairs: minimum and maximum on each axis, ties ordered by candidate_id."
        ),
        "n": n,
        "largest_rank_gaps": ranked[:3] if n >= 3 else [],
        "extreme_contrasts": extremes,
        "selection_note": "Fixed descriptive selection after freezing all first judgments; no scoring changes.",
    }


def _fully_scored(judgment: Judgment) -> bool:
    return (
        judgment.status == "scored"
        and judgment.execution_status == "succeeded"
        and judgment.error_category is None
        and judgment.score_continuous is not None
        and math.isfinite(judgment.score_continuous)
    )


def _direct(judgment: Judgment) -> float | None:
    factor = judgment.factors.get("direct_overall")
    if factor and factor.type == "score" and factor.assessability == "assessable" and not factor.error:
        return factor.score
    return None


def _write(path: Path, content: str):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _verified_artifact(directory: Path, manifest: dict, filename: str) -> list[dict]:
    """Hash and decode the same bytes, after all pre-metric checks have passed."""
    metadata = manifest.get("artifacts", {}).get(filename)
    if not isinstance(metadata, dict):
        raise ValueError(f"Missing source artifact integrity declaration: {filename}")
    try:
        body = (directory / filename).read_bytes()
    except OSError as exc:
        raise ValueError(f"Source artifact is unavailable: {filename}") from exc
    if hashlib.sha256(body).hexdigest() != metadata.get("sha256") or len(body) != metadata.get("size_bytes"):
        raise ValueError(f"Source artifact checksum mismatch: {filename}")
    try:
        return [json.loads(line) for line in body.decode("utf-8").splitlines() if line.strip()]
    except ValueError as exc:
        raise ValueError(f"Source artifact is invalid: {filename}") from exc


def _collection_requirements() -> str:
    return """# Remaining collection requirements

This corpus is permanently development-only. Its untimed, selected metric
snapshots cannot supply a breakout event rate, 48-hour labels, calibrated
probabilities, or a valid final-test population. This report does not estimate
those quantities from final-test outcomes.

Before predictive research, collect exact timezone-aware publication times and
48-hour observations (the configured 48 ± 1 hour tolerance), observation and
availability timestamps, an explicit views source for the current target, and
organic/paid/giveaway distribution provenance. Keep impressions separate; they
cannot substitute for views in the current target or its historical baseline.
Record original source identities and revisions, author identities, follower
counts if legitimately available at the prediction cutoff, and at least 10 valid
historical observations from the preceding 20 eligible posts for each author's
baseline. History must be available by each prediction cutoff. Supply missing
parent/quote context and actual media descriptions separately and honestly.

Declare the intended sampling frame, population, inclusion/exclusion rules,
chronological collection window, audience and profile before collecting labels.
Collect consecutive or randomly selected posts rather than only popular posts,
include low and zero outcomes, and retain failures and context exclusions. Seek
independent authors; many observations from one author are not independent author
support. Reserve fresh, unexposed final-test data separately; this diagnostic
corpus and inspected development outcomes can never become untouched holdouts.

Use the development-only readiness report on an appropriate newly collected
population to estimate event frequency, required positive outcomes, exclusions,
and independent-author support. A collection target is not a validated power
calculation. Do not tune collection from final-test outcomes. The current
5,000-row evaluation cap is unchanged: rare events, exclusions, chronological
partitions, calibration, and author-held-out checks may require more data than
one capped evaluation can support. Report that limit explicitly; do not bypass
it by repeatedly searching capped slices or inspecting the reserved test set.

Any revised audience, humor/community construct, questions, or scoring rule
requires a separately versioned, preregistered experiment and new untouched data.
No automatic predictor promotion is authorized, and all existing promotion
thresholds and holdout protections remain in force.
"""


def write_diagnostic_report(directory: Path) -> dict:
    """Verify a private live freeze, then write private descriptive artifacts.

    Authorization and frozen hashes establish binding within this local workflow,
    not an external signature proving provenance against a malicious file editor.
    No metrics are read when the freeze or any pre-metric binding is invalid.
    """
    from .diagnostics import _private_directory

    directory = _private_directory(directory)
    protocol, freeze, manifest, judgments = _verify_freeze(directory)
    sources = [
        DiagnosticSourceRecord.model_validate(row)
        for row in _verified_artifact(directory, manifest, "normalized_source.jsonl")
    ]
    ids = [record.candidate_id for record in sources]
    if len(ids) != len(set(ids)) or set(ids) != set(manifest["candidate_ids"]):
        raise ValueError("Normalized source rows do not match the source manifest")
    if any(record.source_id != manifest["source_id"] for record in sources):
        raise ValueError("Normalized source identifiers do not match the source manifest")
    selected = set(protocol["candidate_ids"])
    if any(record.initial_cohort_eligible != (record.candidate_id in selected) for record in sources):
        raise ValueError("Normalized source eligibility changed")
    # This is the first metric access, after every freeze/protocol/execution check.
    metric_rows = [
        MetricSnapshot.model_validate(row)
        for row in _verified_artifact(directory, manifest, "metric_snapshots.jsonl")
    ]
    metrics = {metric.candidate_id: metric for metric in metric_rows}
    if len(metrics) != len(metric_rows):
        raise ValueError("Duplicate metric snapshots are ambiguous")
    if any(
        metric.candidate_id not in ids or metric.source_id != manifest["source_id"] for metric in metric_rows
    ):
        raise ValueError("Unexpected metric snapshot identity")
    exclusions = {
        row["candidate_id"]: row.get("reason_codes", []) for row in manifest.get("excluded_candidates", [])
    }
    table, comparison_rows = [], []
    for source in sources:
        candidate_id = source.candidate_id
        judgment = judgments.get(candidate_id)
        metric = metrics.get(candidate_id)
        if judgment and metric and metric.candidate_version != judgment.candidate_version:
            raise ValueError("Metric snapshot candidate version differs from its frozen judgment")
        factor_values = judgment.factors if judgment else {}
        certainty = {
            "meaning": "Provider answer certainty, not a calibrated forecast probability.",
            "by_question": {
                key: factor.confidence
                for key, factor in factor_values.items()
                if factor.confidence is not None
            },
            "low_certainty_flags": [
                flag
                for flag in (judgment.review_flags if judgment else [])
                if flag.startswith("low_answer_certainty:")
            ],
            "noul_has_separate_confidence": False,
        }
        table.append(
            {
                "candidate_id": candidate_id,
                "source": source.model_dump(mode="json"),
                "cohort_status": "initial" if candidate_id in selected else "excluded",
                "exclusion_reasons": exclusions.get(candidate_id, []),
                "judgment": judgment.model_dump(mode="json") if judgment else None,
                "status": judgment.status
                if judgment
                else "unexecuted"
                if candidate_id in selected
                else "excluded",
                "execution_status": judgment.execution_status if judgment else "not_attempted",
                "evidence_completeness": judgment.evidence_completeness if judgment else "unknown",
                "composite_continuous": judgment.score_continuous if judgment else None,
                "editorial_1_to_5": judgment.score_1_to_5 if judgment else None,
                "direct_overall": _direct(judgment) if judgment else None,
                "factors": {key: factor.model_dump(mode="json") for key, factor in factor_values.items()},
                "certainty": certainty,
                "review_flags": judgment.review_flags if judgment else [],
                "metric_snapshot": metric.model_dump(mode="json") if metric else None,
                "included_in_descriptive_associations": bool(judgment and _fully_scored(judgment)),
            }
        )
        if judgment and _fully_scored(judgment):
            comparison_rows.append(
                {
                    "candidate_id": candidate_id,
                    "composite": judgment.score_continuous,
                    "direct": _direct(judgment),
                    "views": metric.views if metric else None,
                    "supplied_engagement_score": metric.supplied_engagement_score if metric else None,
                }
            )
    counts = {
        status: sum(j.status == status for j in judgments.values())
        for status in ("scored", "partial", "abstained", "failed")
    }
    counts["unexecuted"] = len(freeze["unexecuted_candidate_ids"])
    total, initial, scored = len(sources), len(selected), len(comparison_rows)
    coverage = {
        "total_records": total,
        "initial_cohort_records": initial,
        "excluded_records": total - initial,
        "executed_records": len(judgments),
        "status_counts": counts,
        "execution_status_counts": dict(Counter(j.execution_status for j in judgments.values())),
        "fully_scored_records": scored,
        "fully_scored_fraction_of_all_records": scored / total if total else None,
        "fully_scored_fraction_of_initial_cohort": scored / initial if initial else None,
        "records_without_metric_snapshot": sum(row["metric_snapshot"] is None for row in table),
        "metric_missingness_all_records": {
            name: sum(getattr(metrics.get(cid), name, None) is None for cid in ids)
            for name in (
                "views",
                "likes",
                "reposts",
                "quotes",
                "replies",
                "bookmarks",
                "supplied_engagement_score",
            )
        },
    }
    report = {
        "report_version": "diagnostic_report_v1",
        "created_at": now().isoformat(),
        "research_role": "diagnostic_only",
        "protocol_hash": protocol["protocol_hash"],
        "freeze_hash": freeze["freeze_hash"],
        "configuration": {
            key: protocol[key]
            for key in ("model", "profile_id", "audience", "rubric_version", "rubric_hash", "code_commit")
        },
        "coverage": coverage,
        "associations": {
            f"{x}_vs_{y}": _association(comparison_rows, x, y)
            for x in ("composite", "direct")
            for y in ("views", "supplied_engagement_score")
        },
        "disagreements": _disagreements(comparison_rows),
        "construct_hypothesis": (
            "Hypothesis only: the fixed technical-usefulness audience/rubric may describe community humor "
            "or identity expression differently from supplied engagement snapshots. Rank contrasts alone "
            "do not establish this explanation; inspect the selected original cases and missing context "
            "before proposing any separately versioned future questions."
        ),
        "limitations": _LIMITATIONS,
        "predictor_promoted": False,
        "predictive_accuracy_established": False,
        "calibrated_probability_established": False,
    }
    lines = [
        "# Private development-only diagnostic report",
        "",
        f"Protocol `{protocol['protocol_hash']}`; first-judgment freeze `{freeze['freeze_hash']}`.",
        "",
        f"All {total} source records are retained; {initial} belong to the frozen initial cohort. "
        f"{scored} are fully scored and eligible for descriptive associations. "
        "Partial, abstained, failed, unexecuted, and excluded records remain in diagnostic_table.jsonl.",
        "",
        f"Initial-cohort status counts: `{canonical(counts)}`.",
        "",
        "The continuous editorial composite and the independent direct-overall baseline remain separate. "
        "Metric snapshots are joined only after validating the first-judgment freeze. "
        "No model input, question, audience, score, or threshold is changed by this report.",
        "",
        "| Descriptive association | Complete pairs | Spearman | Undefined reason |",
        "| --- | ---: | ---: | --- |",
    ]
    for name, relation in report["associations"].items():
        coefficient = "undefined" if relation["spearman"] is None else f"{relation['spearman']:.4f}"
        lines.append(
            f"| {name} | {relation['n']} | {coefficient} | {', '.join(relation['undefined_reasons']) or '—'} |"
        )
    lines += [
        "",
        "Counts, ties, and missingness are recorded in diagnostic_report.json. "
        "These are selected-corpus descriptions without p-values, fitted forecasts, or accuracy claims.",
        "",
        report["disagreements"]["rule"],
        "",
        "Selected candidate IDs and numeric contrasts are in diagnostic_report.json; their complete source "
        "text, limitations, factors, certainty, flags, status, and separate snapshots are in diagnostic_table.jsonl. "
        "Missing context, audience mismatch, construct differences, exposure, and chance remain alternative explanations.",
        "",
        report["construct_hypothesis"],
        "",
        *[f"- {limitation}" for limitation in _LIMITATIONS],
        "",
        "Remaining data-collection requirements are in collection_requirements.md. "
        "No predictor is trained or promoted by this report.",
        "",
    ]
    _write(directory / "diagnostic_table.jsonl", "".join(canonical(row) + "\n" for row in table))
    _write(directory / "diagnostic_report.json", canonical(report) + "\n")
    _write(directory / "diagnostic_report.md", "\n".join(lines))
    _write(directory / "collection_requirements.md", _collection_requirements())
    return report
