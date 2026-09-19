"""Isolated development-only history-based prediction comparison.

Compares fixed historical estimators against Jev-reranked ones on snapshot
outcomes. Scope is permanently development_only: no training claim, no
validation relabeling, no promotion path, no holdout consumption.
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime

from .state import similarity

SCOPE = "development_only"
SHORTLIST_K = 8
NEAR_DUP_JACCARD = 0.8
WEIGHT_SMOOTHING = 0.1
MIN_FORMAT_SUPPORT = 3


def _log_views(record) -> float:
    return math.log(record["metrics"]["views"] + 1)


def eligible_evidence(target: dict, pool: list[dict], *, exclude_ids: set[str]) -> list[dict]:
    """Strictly older, text+outcome-bearing, non-excluded, non-near-duplicate history."""
    out = []
    for record in pool:
        if record["post_id"] in exclude_ids or record["post_id"] == target["post_id"]:
            continue
        if not (record.get("text") or "").strip() or record.get("metrics", {}).get("views") is None:
            continue
        if record["claimed_date"] >= target["claimed_date"]:
            continue
        if similarity(target["text"], record["text"]) >= NEAR_DUP_JACCARD:
            continue
        out.append(record)
    return out


def retrieve_shortlist(target_text: str, evidence: list[dict], k: int = SHORTLIST_K) -> list[tuple]:
    """Deterministic top-k by Jaccard similarity; post_id tiebreak; internal dedupe."""
    ranked = sorted(
        ((similarity(target_text, r["text"]), r["post_id"], r) for r in evidence), key=lambda t: (-t[0], t[1])
    )
    shortlist: list[tuple] = []
    for sim, _, record in ranked:
        if any(similarity(record["text"], kept["text"]) >= NEAR_DUP_JACCARD for _, kept in shortlist):
            continue
        shortlist.append((sim, record))
        if len(shortlist) >= k:
            break
    return shortlist


def _weights(scores: list[float]) -> list[float]:
    shifted = [s + WEIGHT_SMOOTHING for s in scores]
    total = math.fsum(shifted)
    return [s / total for s in shifted]


def weighted_log_mean(pairs: list[tuple], score_fn) -> tuple[float | None, list[float]]:
    """Shared outcome estimator: weighted mean of ln(views) over (record, score) pairs."""
    if not pairs:
        return None, []
    weights = _weights([score_fn(record, score) for record, score in pairs])
    return math.fsum(w * _log_views(r) for w, (r, _) in zip(weights, pairs)), weights


def method_a(target: dict, evidence: list[dict]) -> tuple[float | None, str, int]:
    """Account/format historical baseline with an explicit fallback chain."""
    fmt = [
        r
        for r in evidence
        if r.get("post_type") == target.get("post_type") and target.get("post_type") != "unknown"
    ]
    if len(fmt) >= MIN_FORMAT_SUPPORT:
        return statistics.median(_log_views(r) for r in fmt), "format", len(fmt)
    if evidence:
        return statistics.median(_log_views(r) for r in evidence), "account_fallback", len(evidence)
    return None, "uncovered", 0


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i + 1
        while j < len(order) and values[order[j]] == values[order[i]]:
            j += 1
        for index in order[i:j]:
            ranks[index] = (i + 1 + j) / 2
        i = j
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Descriptive Spearman with average ranks; None when undefined."""
    if len(xs) != len(ys) or len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    var = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return max(-1.0, min(1.0, cov / var)) if var else None


def run_target(target: dict, evidence: list[dict], noul_map: dict | None) -> dict:
    """Estimate one target with methods A/B/C; C only where Nouls cover the shortlist."""
    actual = _log_views(target)
    shortlist = retrieve_shortlist(target["text"], evidence)
    est_a, flag_a, support_a = method_a(target, evidence)
    row: dict = {
        "post_id": target["post_id"],
        "actual_log_views": actual,
        "scope": SCOPE,
        "shortlist": [
            {"post_id": r["post_id"], "similarity": s, "views": r["metrics"]["views"]} for s, r in shortlist
        ],
    }
    if shortlist:
        est_b, weights_b = weighted_log_mean([(r, s) for s, r in shortlist], lambda _r, s: s)
        row["B"] = {"estimate": est_b, "weights": weights_b, "fallback": None, "evidence": row["shortlist"]}
    else:
        row["B"] = {"estimate": est_a, "weights": [], "fallback": "method_A", "evidence": []}
    key = target["post_id"]
    if noul_map is not None and shortlist and all((key, r["post_id"]) in noul_map for _, r in shortlist):
        pairs = [(r, noul_map[(key, r["post_id"])]) for _, r in shortlist]
        est_c, weights_c = weighted_log_mean(pairs, lambda _r, n: n)
        row["C"] = {
            "estimate": est_c,
            "weights": weights_c,
            "fallback": None,
            "evidence": [
                {"post_id": r["post_id"], "noul": n, "views": r["metrics"]["views"]} for r, n in pairs
            ],
        }
    elif shortlist:
        row["C"] = {"estimate": None, "weights": [], "fallback": "noul_unavailable", "evidence": []}
    else:
        row["C"] = {"estimate": est_a, "weights": [], "fallback": "method_A", "evidence": []}
    row["A"] = {"estimate": est_a, "fallback": None if flag_a == "format" else flag_a, "support": support_a}
    return row


def summarize(rows: list[dict]) -> dict:
    """Prediction error, ranking usefulness, and coverage per method (shared code)."""
    summary = {"scope": SCOPE, "targets": len(rows), "methods": {}}
    for method in ("A", "B", "C"):
        covered = [r for r in rows if r[method]["estimate"] is not None]
        errors = [abs(r[method]["estimate"] - r["actual_log_views"]) for r in covered]
        preds = [r[method]["estimate"] for r in covered]
        actuals = [r["actual_log_views"] for r in covered]
        fallbacks = [r[method]["fallback"] for r in covered if r[method]["fallback"]]
        summary["methods"][method] = {
            "covered": len(covered),
            "mae_log": statistics.fmean(errors) if errors else None,
            "rmse_log": math.sqrt(sum(e * e for e in errors) / len(errors)) if errors else None,
            "median_abs_err_log": statistics.median(errors) if errors else None,
            "spearman": spearman(preds, actuals),
            "fallback_counts": {f: fallbacks.count(f) for f in sorted(set(fallbacks))},
        }
    return summary


def claimed_date(value) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
