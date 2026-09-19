"""Private, temporal evaluation and manually gated forecast inference.

Synthetic demonstrations exercise software; they are never evidence about Jev.
Models are serialized as numeric JSON rather than executable pickle artifacts.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import math
import re
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score

from .contracts import canonical, digest, now, uid
from .storage import Store

CONFIG = json.loads((Path(__file__).parent / "config/evaluation_v1.json").read_text())
RUBRIC = json.loads((Path(__file__).parent / "config/rubric_v1.json").read_text())
SEMANTIC_NAMES = [x for x in RUBRIC["profiles"]["text_core_v1"]]
METADATA_NAMES = ["log_followers", "log_baseline_views", "log_baseline_count"]
METHODS = ["constant", "direct_jev", "editorial", "metadata_only", "jev_plus_metadata", "jev_only"]
SPLITS = ("train", "selection", "calibration", "test")


def _date(value: str | datetime) -> datetime:
    d = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(d, datetime) or d.tzinfo is None:
        raise ValueError("Evaluation timestamps must carry a timezone")
    return d.astimezone(timezone.utc)


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _key(candidate: dict) -> str:
    return f'{candidate["candidate_id"]}:{candidate.get("candidate_version", 1)}'


def _log(value: Any) -> float | None:
    return math.log1p(value) if _finite(value) and value >= 0 else None


def _valid_window(candidate: dict, outcome: dict, definition: dict) -> bool:
    if not candidate.get("published_at") or not outcome.get("observed_at"):
        return False
    elapsed = (_date(outcome["observed_at"]) - _date(candidate["published_at"])).total_seconds() / 3600
    stated = outcome.get("elapsed_hours")
    return (_finite(stated) and abs(elapsed - stated) <= 0.01 and
            abs(elapsed - definition["window_hours"]) <= definition["window_tolerance_hours"] and
            _date(outcome["available_at"]) >= _date(outcome["observed_at"]))


def label_candidate(candidate: dict, context: dict, candidates: list[dict], outcomes: list[dict],
                    *, task: str = "breakout_48h_v1", as_of: datetime | None = None) -> dict:
    """Build labels only from measured organic views at the specified window.

    History is reconstructed from outcome observations, never trusted from a
    supplied aggregate. An earlier publication with a future measurement cannot
    enter the baseline. Conflicting same-source observations are unavailable.
    """
    if task not in CONFIG["label_definitions"]:
        raise ValueError(f"Unknown label definition: {task}")
    definition = CONFIG["label_definitions"][task]
    result = {"label_definition_id": task, "status": "unavailable", "label": None,
              "absolute_reach": None, "relative_outperformance": None,
              "baseline_views": None, "baseline_sample_count": 0,
              "baseline_observation_ids": [], "baseline_post_type_policy": definition["post_type_policy"]}
    cutoff = _date(context["prediction_cutoff"])
    current = as_of or now()
    if not candidate.get("published_at"):
        return dict(result, reason="publication_timestamp_missing")
    publication = _date(candidate["published_at"])
    if cutoff > publication:
        return dict(result, reason="prediction_after_publication")
    if candidate.get("distribution") != "organic":
        return dict(result, reason="candidate_distribution_not_organic")
    target = [o for o in outcomes if _key(o) == _key(candidate)]
    selected = [o for o in target if o.get("metric") == "views" and o.get("distribution") == "organic"
                and _valid_window(candidate, o, definition) and _finite(o.get("views"))
                and _date(o["available_at"]) <= current
                and bool(o.get("synthetic")) == bool(candidate.get("synthetic"))]
    if not selected:
        pending = current < publication + timedelta(hours=definition["window_hours"] + definition["window_tolerance_hours"])
        reason = "immature_outcome" if pending else "no_qualifying_48h_view_observation"
        return dict(result, status="pending" if pending else "unavailable", reason=reason)
    sources = {o.get("source") for o in selected}
    if len(sources) != 1:
        return dict(result, reason="ambiguous_metric_sources")
    selected.sort(key=lambda o: (abs(o["elapsed_hours"] - definition["window_hours"]), o["available_at"], o["observation_id"]))
    observation = selected[0]
    if any(o["observed_at"] == observation["observed_at"] and o["views"] != observation["views"] for o in selected):
        return dict(result, reason="conflicting_measurements")
    result.update(views=observation["views"], source=observation["source"], observation_id=observation["observation_id"],
                  label_available_at=observation["available_at"], absolute_reach=int(observation["views"] >= definition["absolute_views"]))
    by_candidate = defaultdict(list)
    for o in outcomes:
        by_candidate[_key(o)].append(o)
    history = []
    seen_posts = set()
    for c in sorted(candidates, key=lambda c: c.get("published_at") or "", reverse=True):
        if (c.get("candidate_id") in seen_posts or c.get("candidate_id") == candidate["candidate_id"]
            or not candidate.get("author_id") or c.get("author_id") != candidate.get("author_id")
            or c.get("post_type") != candidate.get("post_type") or c.get("distribution") != "organic"
            or not c.get("published_at") or _date(c["published_at"]) >= cutoff
            or bool(c.get("synthetic")) != bool(candidate.get("synthetic"))):
            continue
        eligible = [o for o in by_candidate[_key(c)] if o.get("metric") == "views"
                    and o.get("source") == observation["source"] and o.get("distribution") == "organic"
                    and _finite(o.get("views")) and _valid_window(c, o, definition)
                    and _date(o["available_at"]) <= cutoff
                    and bool(o.get("synthetic")) == bool(candidate.get("synthetic"))]
        if not eligible:
            continue
        eligible.sort(key=lambda o: (abs(o["elapsed_hours"] - 48), o["available_at"], o["observation_id"]))
        chosen = eligible[0]
        if any(o["observed_at"] == chosen["observed_at"] and o["views"] != chosen["views"] for o in eligible):
            continue
        seen_posts.add(c["candidate_id"])
        history.append(chosen)
        if len(history) >= definition["maximum_baseline_posts"]:
            break
    result.update(baseline_sample_count=len(history), baseline_observation_ids=[o["observation_id"] for o in history])
    baseline = float(np.median([o["views"] for o in history])) if history else None
    result["baseline_views"] = baseline
    if task == "absolute_48h_v1":
        return dict(result, label=result["absolute_reach"], status="eligible", reason=None)
    if len(history) < definition["minimum_baseline_posts"]:
        return dict(result, reason="insufficient_as_of_baseline")
    if baseline is None or baseline <= 0:
        return dict(result, reason="zero_or_missing_baseline")
    relative = observation["views"] / baseline
    return dict(result, relative_outperformance=relative, label=int(result["absolute_reach"] and relative >= definition["relative_multiplier"]),
                status="eligible", reason=None)


def _feature_row(candidate: dict, context: dict, judgment: dict, label: dict, state: dict | None = None) -> dict:
    cutoff = _date(context["prediction_cutoff"])
    historical = context.get("historical") or {}
    valid_history = (historical.get("observed_at") and historical.get("available_at") and
                     _date(historical["observed_at"]) <= cutoff and _date(historical["available_at"]) <= cutoff)
    followers = historical.get("followers") if valid_history else None
    values = {name: judgment["factors"][name]["score"] for name in SEMANTIC_NAMES}
    values.update(log_followers=_log(followers), log_baseline_views=_log(label["baseline_views"]),
                  log_baseline_count=_log(label["baseline_sample_count"]))
    direct = judgment["factors"].get("overall", judgment["factors"].get("direct_overall", {})).get("score")
    return {"key": _key(candidate), "candidate_id": candidate["candidate_id"], "judgment_id": judgment["judgment_id"],
            "cutoff": context["prediction_cutoff"], "label_available_at": label["label_available_at"],
            "label": label["label"], "label_details": label, "features": values,
            "direct": direct, "editorial": judgment["score_continuous"],
            "author": candidate.get("author_id") or f'unknown:{candidate["candidate_id"]}',
            "author_known": bool(candidate.get("author_id")), "thread_id": candidate.get("thread_id"),
            "text": candidate["text"], "niche": candidate.get("niche", "unknown"),
            "post_type": candidate.get("post_type", "unknown"), "language": candidate.get("language", "unknown"),
            "account_size": "unknown" if followers is None else "under_1k" if followers < 1000 else "1k_to_10k" if followers < 10000 else "10k_plus",
            "execution_mode": judgment["execution_mode"], "profile_id": judgment["profile_id"],
            "audience_version": judgment["audience_version"], "audience_id": judgment.get("audience_id"),
            "rubric_version": judgment["rubric_version"], "model_requested": judgment.get("model_requested"),
            "model_returned": judgment.get("model_returned"), "reference_set_hash": judgment.get("reference_set_hash"),
            "references": context.get("references", []), "synthetic": bool(candidate.get("synthetic")),
            "sampling_provenance": candidate.get("provenance", "unknown"), "state": state}


def build_dataset(store: Store, *, task: str = "breakout_48h_v1", synthetic: bool = False, max_rows: int = 5000,
                  cohort: dict | None = None) -> dict:
    cohort = cohort or {}
    permitted = {"profile_id", "audience_id", "audience_version", "rubric_version", "model_requested", "execution_mode"}
    if set(cohort) - permitted:
        raise ValueError("Unknown cohort filter; use explicit judgment configuration fields")
    candidates, outcomes = store.list("candidate"), store.list("outcome")
    runs = {r.get("judgment_id"): r for r in store.list("run") if r.get("judgment_id")}
    judgments = defaultdict(list)
    for j in store.list("judgment"):
        judgments[_key(j)].append(j)
    rows, exclusions, labels = [], [], []
    for candidate in candidates:
        key = _key(candidate)
        if bool(candidate.get("synthetic")) != synthetic:
            exclusions.append({"key": key, "reason": "synthetic_real_cohort_separation"})
            continue
        options = [j for j in judgments.get(key, []) if all(j.get(k) == v for k, v in cohort.items())]
        # Deterministic earliest completed judgment; reruns cannot select favorable results.
        options.sort(key=lambda j: (j.get("created_at", ""), j["judgment_id"]))
        judgment = options[0] if options else None
        run = runs.get(judgment["judgment_id"]) if judgment else None
        if not judgment or not run or not run.get("request", {}).get("context"):
            exclusions.append({"key": key, "reason": "missing_judgment_or_prediction_context"})
            continue
        context = run["request"]["context"]
        label = label_candidate(candidate, context, candidates, outcomes, task=task)
        labels.append(dict(label, key=key))
        if label["status"] != "eligible":
            exclusions.append({"key": key, "reason": label["reason"], "label_status": label["status"]})
            continue
        if judgment.get("status") != "scored" or not _finite(judgment.get("score_continuous")):
            exclusions.append({"key": key, "reason": "judgment_abstention_or_failure"})
            continue
        if not synthetic and judgment.get("execution_mode") != "live":
            exclusions.append({"key": key, "reason": "mock_cannot_establish_real_predictive_evidence"})
            continue
        factors = judgment.get("factors", {})
        direct = factors.get("overall", factors.get("direct_overall", {})).get("score")
        if any(not _finite(factors.get(n, {}).get("score")) or factors[n].get("assessability") != "assessable" for n in SEMANTIC_NAMES) or not _finite(direct):
            exclusions.append({"key": key, "reason": "incomplete_common_feature_cohort"})
            continue
        if candidate.get("content_available_at") and _date(candidate["content_available_at"]) > _date(context["prediction_cutoff"]):
            exclusions.append({"key": key, "reason": "candidate_not_available_at_cutoff"})
            continue
        rows.append(_feature_row(candidate, context, judgment, label, run.get("state")))
    if len(rows) > max_rows:
        raise ValueError(f"Eligible dataset exceeds explicit row limit {max_rows}; narrow the corpus, do not silently subsample")
    signatures = Counter((r["profile_id"], r["audience_id"], r["audience_version"], r["rubric_version"], r["model_requested"], r["execution_mode"]) for r in rows)
    if len(signatures) > 1:
        # A mixed audience/rubric cohort cannot support one coherent predictor.
        exclusions.extend({"key": r["key"], "reason": "mixed_judgment_configuration_select_a_coherent_corpus"} for r in rows)
        rows = []
    return {"rows": rows, "exclusions": exclusions, "labels": labels, "total_candidates": len(candidates),
            "coverage": len(rows) / len(candidates) if candidates else None,
            "exclusion_counts": dict(Counter(e["reason"] for e in exclusions)),
            "configuration_signatures": [list(x) for x in signatures]}


def eligibility(store: Store, *, task: str = "breakout_48h_v1", synthetic: bool = False, max_rows: int = 5000,
                cohort: dict | None = None) -> dict:
    data = build_dataset(store, task=task, synthetic=synthetic, max_rows=max_rows, cohort=cohort)
    rows = data.pop("rows")
    return dict(data, eligible_rows=len(rows), positives=sum(r["label"] for r in rows),
                eligible_candidate_ids=[r["key"] for r in rows], label_definition=CONFIG["label_definitions"][task])


def _clusters(rows: list[dict]) -> list[str]:
    """Connected near-duplicate/thread groups; all later-partition members purge."""
    parents = list(range(len(rows)))
    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    def union(a, b):
        parents[root(b)] = root(a)
    texts = [set(re.findall(r"\w+", r["text"].lower())) for r in rows]
    threshold = RUBRIC["policy"]["near_duplicate_jaccard"]
    for i in range(len(rows)):
        for j in range(i):
            if rows[i].get("thread_id") and rows[i]["thread_id"] == rows[j].get("thread_id"):
                union(i, j)
            elif texts[i] and texts[j] and min(len(texts[i]), len(texts[j])) / max(len(texts[i]), len(texts[j])) >= threshold:
                if len(texts[i] & texts[j]) / len(texts[i] | texts[j]) >= threshold:
                    union(i, j)
    return [f"cluster:{root(i)}" for i in range(len(rows))]


def split_dataset(rows: list[dict]) -> tuple[dict[str, list[dict]], dict]:
    rows = sorted(rows, key=lambda r: (_date(r["cutoff"]), r["key"]))
    n = len(rows)
    fractions = CONFIG["split_fractions"]
    a = int(n * fractions["train"])
    b = int(n * (fractions["train"] + fractions["selection"]))
    c = int(n * (fractions["train"] + fractions["selection"] + fractions["calibration"]))
    partitions = {"train": rows[:a], "selection": rows[a:b], "calibration": rows[b:c], "test": rows[c:]}
    boundaries = {name: partitions[name][0]["cutoff"] if partitions[name] else None for name in SPLITS}
    removals = []
    # Same cutoff never straddles partitions: purge earlier copies, never move futures backward.
    for i, name in enumerate(SPLITS[:-1]):
        boundary = boundaries[SPLITS[i + 1]]
        if boundary:
            kept = []
            for row in partitions[name]:
                if _date(row["cutoff"]) >= _date(boundary) or _date(row["label_available_at"]) > _date(boundary):
                    removals.append({"key": row["key"], "split": name, "reason": "not_mature_at_next_boundary"})
                else:
                    kept.append(row)
            partitions[name] = kept
    clusters = dict(zip([r["key"] for r in rows], _clusters(rows)))
    first_split = {}
    candidate_split = {r["candidate_id"]: name for name in SPLITS for r in partitions[name]}
    for name in SPLITS:
        kept = []
        for row in partitions[name]:
            group = clusters[row["key"]]
            if group in first_split and first_split[group] != name:
                removals.append({"key": row["key"], "split": name, "reason": "near_duplicate_or_thread_cross_partition"})
            elif any(ref.get("split") == "test" or candidate_split.get(ref.get("candidate_id")) in SPLITS[SPLITS.index(name)+1:]
                     or _date(ref["available_at"]) > _date(row["cutoff"]) for ref in row.get("references", [])):
                removals.append({"key": row["key"], "split": name, "reason": "reference_split_or_time_leakage"})
            else:
                first_split[group] = name
                kept.append(dict(row, cluster=group))
        partitions[name] = kept
    authors = sorted({r["author"] for r in rows if r.get("author_known", True)})
    heldout = [a for a in authors if int(digest(a)[:8], 16) / 0x100000000 < CONFIG["author_holdout_fraction"]] if len(authors) >= 5 else []
    for name in SPLITS[:-1]:
        removed = [r for r in partitions[name] if r["author"] in heldout]
        removals.extend({"key": r["key"], "split": name, "reason": "author_holdout"} for r in removed)
        partitions[name] = [r for r in partitions[name] if r["author"] not in heldout]
    manifest = {"boundaries": boundaries, "removals": removals, "heldout_authors": heldout,
                "partitions": {name: [r["key"] for r in partitions[name]] for name in SPLITS},
                "counts": {name: {"rows": len(partitions[name]), "positives": sum(r["label"] for r in partitions[name])} for name in SPLITS},
                "policy": "chronological_50_15_15_20; mature_at_next_boundary; purge_later_cluster_members; deterministic_author_holdout"}
    return partitions, manifest


def _matrix(rows: list[dict], names: list[str]) -> np.ndarray:
    return np.array([[r["features"].get(k) if r["features"].get(k) is not None else np.nan for k in names] for r in rows], dtype=float)


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -40, 40)))


def fit_model(rows: list[dict], names: list[str], c: float = 1) -> dict:
    x = _matrix(rows, names)
    medians = [float(np.median(col[~np.isnan(col)])) if np.any(~np.isnan(col)) else 0.0 for col in x.T]
    missing = np.isnan(x).astype(float)
    imputed = np.where(np.isnan(x), np.array(medians), x)
    x = np.concatenate([imputed, missing], axis=1)
    means, scales = x.mean(axis=0), x.std(axis=0)
    scales[scales == 0] = 1
    fitted = LogisticRegression(C=c, max_iter=1000, random_state=20260918).fit((x - means) / scales, [r["label"] for r in rows])
    return {"kind": "regularized_logistic", "names": names, "medians": medians,
            "means": means.tolist(), "scales": scales.tolist(), "coefficients": fitted.coef_[0].tolist(),
            "intercept": float(fitted.intercept_[0]), "C": c, "fitted_on": [r["key"] for r in rows]}


def raw_predict(model: dict, rows: list[dict]) -> np.ndarray:
    if model["kind"] == "scalar":
        return np.array([r[model["field"]] for r in rows], dtype=float)
    x = _matrix(rows, model["names"])
    missing = np.isnan(x).astype(float)
    x = np.concatenate([np.where(np.isnan(x), np.array(model["medians"]), x), missing], axis=1)
    return ((x - np.array(model["means"])) / np.array(model["scales"])) @ np.array(model["coefficients"]) + model["intercept"]


def _calibrate(model: dict, rows: list[dict]) -> dict:
    raw = raw_predict(model, rows)
    fitted = LogisticRegression(C=1, max_iter=1000).fit(raw.reshape(-1, 1), [r["label"] for r in rows])
    return {"kind": "platt_logistic", "coefficient": float(fitted.coef_[0, 0]), "intercept": float(fitted.intercept_[0]),
            "fitted_on": [r["key"] for r in rows], "partition": "calibration"}


def model_predict(model: dict, rows: list[dict]) -> np.ndarray:
    if model["kind"] == "constant":
        return np.full(len(rows), model["prevalence"])
    raw = raw_predict(model, rows)
    cal = model["calibrator"]
    return _sigmoid(raw * cal["coefficient"] + cal["intercept"])


def metrics(labels: list[int] | np.ndarray, probabilities: list[float] | np.ndarray) -> dict:
    y, p = np.array(labels, dtype=int), np.array(probabilities, dtype=float)
    if len(y) == 0:
        return {"rows": 0, "positives": 0, "reason": "empty_cohort", "brier": None, "pr_auc": None, "roc_auc": None, "log_loss": None}
    prevalence = float(y.mean())
    both = len(set(y)) == 2
    output = {"rows": len(y), "positives": int(y.sum()), "prevalence": prevalence,
              "brier": float(brier_score_loss(y, p)), "log_loss": float(log_loss(y, np.clip(p, 1e-8, 1 - 1e-8), labels=[0, 1])),
              "pr_auc": float(average_precision_score(y, p)) if both else None,
              "roc_auc": float(roc_auc_score(y, p)) if both else None,
              "undefined_reason": None if both else "only_one_outcome_class"}
    for fraction in (.1, .2):
        count = max(1, math.ceil(len(y) * fraction))
        ranked = np.argsort(-p, kind="stable")[:count]
        precision = float(y[ranked].mean())
        output[f"top_{int(fraction*100)}pct"] = {"rows": count, "precision": precision,
                                                   "recall": float(y[ranked].sum() / y.sum()) if y.sum() else None,
                                                   "lift": precision / prevalence if prevalence else None,
                                                   "tie_policy": "stable_candidate_order"}
    bins = []
    for low, high in zip(np.linspace(0, 1, 11)[:-1], np.linspace(0, 1, 11)[1:]):
        selected = (p >= low) & ((p < high) if high < 1 else (p <= high))
        bins.append({"lower": float(low), "upper": float(high), "rows": int(selected.sum()),
                     "mean_probability": float(p[selected].mean()) if selected.any() else None,
                     "observed_rate": float(y[selected].mean()) if selected.any() else None})
    output["reliability"] = bins
    output["expected_calibration_error"] = sum(b["rows"] / len(y) * abs(b["mean_probability"] - b["observed_rate"]) for b in bins if b["rows"])
    return output


def _bootstrap(rows: list[dict], predictions: dict[str, np.ndarray], *, replicates: int | None = None) -> dict:
    """Paired account/content resampling preserves both dependence structures."""
    rng = np.random.default_rng(CONFIG["bootstrap_seed"])
    parents = {r["author"]: r["author"] for r in rows}
    def root(a):
        while parents[a] != a:
            parents[a] = parents[parents[a]]
            a = parents[a]
        return a
    content_owner = {}
    for row in rows:
        cluster = row.get("cluster")
        if cluster:
            previous = content_owner.setdefault(cluster, row["author"])
            parents[root(row["author"])] = root(previous)
    authors = sorted({root(r["author"]) for r in rows})
    if len(authors) < 3:
        return {"method": "author_cluster_percentile_bootstrap", "clusters": len(authors), "intervals": {}, "reason": "fewer_than_three_author_clusters"}
    mapping = {a: [i for i, r in enumerate(rows) if root(r["author"]) == a] for a in authors}
    ys = np.array([r["label"] for r in rows])
    samples = defaultdict(list)
    for _ in range(replicates or CONFIG["bootstrap_replicates"]):
        chosen = rng.choice(authors, size=len(authors), replace=True)
        indices = np.array([i for a in chosen for i in mapping[a]])
        y = ys[indices]
        for name, values in predictions.items():
            p = values[indices]
            m = metrics(y, p)
            for key in ("brier", "log_loss", "pr_auc", "roc_auc", "expected_calibration_error"):
                if m.get(key) is not None:
                    samples[f"{name}.{key}"].append(m[key])
            for key in ("top_10pct", "top_20pct"):
                for metric in ("precision", "recall", "lift"):
                    if m[key][metric] is not None:
                        samples[f"{name}.{key}.{metric}"].append(m[key][metric])
        if "metadata_only" in predictions and "jev_plus_metadata" in predictions:
            improvement = np.mean((y - predictions["metadata_only"][indices]) ** 2 - (y - predictions["jev_plus_metadata"][indices]) ** 2)
            samples["paired_brier_improvement_vs_metadata"].append(float(improvement))
    return {"method": "paired_author_and_content_cluster_percentile_bootstrap", "clusters": len(authors),
            "replicates": replicates or CONFIG["bootstrap_replicates"], "coverage": .95,
            "limitations": "Author clusters preserve account dependence; broad event/time shocks may remain correlated. Intervals are not causal evidence.",
            "intervals": {k: {"lower": float(np.quantile(v, .025)), "upper": float(np.quantile(v, .975)), "defined_resamples": len(v)} for k, v in samples.items()}}


def _synthetic_rows(count: int = 400) -> list[dict]:
    """Original generated data with an invented relationship; never Jev results."""
    rng = np.random.default_rng(20260918)
    start = datetime(2022, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(count):
        values = rng.uniform(0, 4, len(SEMANTIC_NAMES))
        values[-1] = rng.uniform(0, 2)
        followers = int(rng.lognormal(7, 1.3))
        baseline = int(rng.lognormal(6, .5))
        probability = float(_sigmoid((values[2] - 2) * 1.4 + .25 * (math.log1p(followers) - 7) - .6))
        label = int(rng.random() < probability)
        cutoff = start + timedelta(days=i * 3)
        quality = float(np.mean(values[:-1]) / 4)
        score = 1 + 4 * max(0, quality - .25 * values[-1] / 4)
        features = dict(zip(SEMANTIC_NAMES, values.tolist()))
        features.update(log_followers=math.log1p(followers), log_baseline_views=math.log1p(baseline), log_baseline_count=math.log1p(20))
        rows.append({"key": f"synthetic-{i}:1", "candidate_id": f"synthetic-{i}", "judgment_id": f"synthetic-judgment-{i}",
                     "cutoff": cutoff.isoformat(), "label_available_at": (cutoff + timedelta(hours=48)).isoformat(),
                     "label": label, "features": features, "direct": float(np.clip(values[2] + rng.normal(0, .7), 0, 4)),
                     "editorial": score, "author": f"synthetic-author-{i % 40}", "author_known": True,
                     "thread_id": None, "text": " ".join(f"token{v}" for v in rng.choice(10000, size=14, replace=False)),
                     "niche": ["production_ai_coding", "indie_saas"][i % 2], "language": "en", "post_type": "original",
                     "account_size": "under_1k" if followers < 1000 else "1k_to_10k" if followers < 10000 else "10k_plus",
                     "execution_mode": "mock", "profile_id": "text_core_v1", "audience_version": "1",
                     "audience_id": "production_ai_coding", "rubric_version": RUBRIC["version"], "model_requested": "synthetic-generator",
                     "model_returned": "synthetic-generator", "references": [], "synthetic": True,
                     "sampling_provenance": "synthetic_generated_software_fixture", "state": None})
    return rows


def _save(store: Store, report: dict) -> dict:
    directory = store.data_dir / "artifacts"
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / f'{report["experiment_id"]}.json'
    path.write_text(json.dumps(report, indent=2, allow_nan=False))
    path.chmod(0o600)
    store.put("experiment", report["experiment_id"], report, replace=True)
    return report


def evaluate(store: Store, *, synthetic: bool = False, task: str = "breakout_48h_v1",
             representative_sampling: bool = False, comparison_population: str = "", holdout_id: str | None = None,
             max_rows: int = 5000, cohort: dict | None = None, **options) -> dict:
    if options:
        raise ValueError(f"Unknown evaluation options: {', '.join(sorted(options))}")
    if task not in CONFIG["label_definitions"]:
        raise ValueError("Unknown label definition")
    if not 40 <= max_rows <= 5000:
        raise ValueError("Evaluation max_rows must be between 40 and 5000")
    dataset = build_dataset(store, task=task, synthetic=synthetic, max_rows=max_rows, cohort=cohort)
    fixture_used = synthetic and not dataset["rows"]
    if fixture_used:
        dataset = {"rows": _synthetic_rows(min(400, max_rows)), "exclusions": [], "labels": [], "total_candidates": min(400, max_rows),
                   "coverage": 1.0, "exclusion_counts": {}, "configuration_signatures": []}
    rows = dataset.pop("rows")
    replay_hash = digest({"rows": rows, "task": task, "synthetic": synthetic,
                          "representative_sampling": representative_sampling, "comparison_population": comparison_population,
                          "holdout_id": holdout_id, "cohort": cohort, "configuration": CONFIG})
    for existing in store.list("experiment"):
        if existing.get("replay_hash") == replay_hash:
            return existing
    report = {"experiment_id": uid(), "created_at": now().isoformat(), "status": "not_ready", "synthetic": synthetic,
              "fixture_used": fixture_used, "predictive_validation": "not_established" if synthetic else "not_yet_evaluated",
              "task": task, "label_definition": CONFIG["label_definitions"][task], "policy_version": CONFIG["version"],
              "dataset_hash": digest(rows), "replay_hash": replay_hash, "cohort_filter": cohort or {},
              "eligibility": dict(dataset, eligible_rows=len(rows), positives=sum(r["label"] for r in rows)),
              "feature_schema": {"semantic": SEMANTIC_NAMES, "metadata": METADATA_NAMES, "version": "features_v1"},
              "promotion_policy": CONFIG["promotion_policy"], "promotion_policy_hash": digest(CONFIG["promotion_policy"]),
              "representative_sampling_attested": bool(representative_sampling), "comparison_population": comparison_population,
              "forecast_available": False, "tier_boundaries": CONFIG["tier_probability_boundaries"],
              "tier_semantics": CONFIG["tier_semantics"], "models": {}, "metrics": {}, "audit": [],
              "limitations": ["Editorial potential, provider certainty and estimated breakout probability are distinct.",
                              "Historical public posts may have been seen in pretraining; prospective shadow evaluation is required to remove that concern.",
                              "No causal claim about rewriting or production X ranking is established."]}
    if synthetic:
        report["limitations"].append("Synthetic labels and features demonstrate software mechanics only; no Jev predictive performance is measured.")
    partitions, manifest = split_dataset(rows)
    report["split_manifest"] = manifest
    report["development_rows"] = [r for name in SPLITS[:-1] for r in partitions[name]]
    report["development_partitions"] = {name: partitions[name] for name in SPLITS[:-1]}
    issues = []
    for name, group in partitions.items():
        positives = sum(r["label"] for r in group)
        if len(group) < CONFIG["minimum_partition_rows"] or positives < CONFIG["minimum_partition_positives"] or len(group) - positives < CONFIG["minimum_partition_negatives"]:
            issues.append(f"{name}: requires at least {CONFIG['minimum_partition_rows']} rows and both outcome classes with at least two examples")
    if issues:
        report["not_ready_reasons"] = issues
        report["promotion"] = {"eligible": False, "reasons": ["insufficient_eligible_mature_partitions"]}
        return _save(store, report)
    train, selection, calibration, test = [partitions[n] for n in SPLITS]
    models = {"constant": {"kind": "constant", "prevalence": float(np.mean([r["label"] for r in train + selection])),
                           "fitted_on": [r["key"] for r in train + selection]}}
    tuning = {}
    selection_prediction = None
    for method, names in (("metadata_only", METADATA_NAMES), ("jev_plus_metadata", SEMANTIC_NAMES + METADATA_NAMES), ("jev_only", SEMANTIC_NAMES)):
        choices = []
        for c in CONFIG["logistic_c_candidates"]:
            model = fit_model(train, names, c)
            loss = float(log_loss([r["label"] for r in selection], _sigmoid(raw_predict(model, selection)), labels=[0, 1]))
            choices.append({"C": c, "selection_log_loss": loss})
        selected = min(choices, key=lambda x: x["selection_log_loss"])
        tuning[method] = {"choices": choices, "selected_C": selected["C"], "partition": "selection"}
        if method == "jev_plus_metadata":
            selection_prediction = _sigmoid(raw_predict(fit_model(train, names, selected["C"]), selection))
        models[method] = fit_model(train + selection, names, selected["C"])
    models["direct_jev"] = {"kind": "scalar", "field": "direct", "fitted_on": []}
    models["editorial"] = {"kind": "scalar", "field": "editorial", "fitted_on": []}
    for name, model in models.items():
        if name != "constant":
            model["calibrator"] = _calibrate(model, calibration)
    report["models"] = models
    report["selection"] = tuning
    frozen_hash = digest({"models": models, "policy": report["promotion_policy"], "tier_boundaries": report["tier_boundaries"],
                          "features": report["feature_schema"], "task": task})
    holdout_key = holdout_id or digest({"task": task, "keys": [r["key"] for r in test]})
    report["holdout_id"] = holdout_key
    report["frozen_candidate_hash"] = frozen_hash
    report["audit"].append({"event": "candidate_frozen_before_test_metrics", "at": now().isoformat(), "hash": frozen_hash})
    # Reserve the holdout atomically before any final labels are used for metric calculations.
    if not synthetic:
        with store.transaction() as db:
            already = db.execute("SELECT body FROM records WHERE kind='holdout' AND id=?", (holdout_key,)).fetchone()
            used = db.execute("SELECT body FROM records WHERE kind='holdout'").fetchall()
            seen = set(k for record in used for k in json.loads(record[0]).get("test_keys", []))
            if already or any(r["key"] in seen for r in test):
                report["status"] = "holdout_already_consumed"
                report["not_ready_reasons"] = ["Final-test rows were previously opened. Use a new untouched or prospective holdout; do not tune against the same test."]
                report["promotion"] = {"eligible": False, "reasons": ["holdout_reuse"]}
            else:
                record = {"holdout_id": holdout_key, "experiment_id": report["experiment_id"], "test_keys": [r["key"] for r in test],
                          "candidate_hash": frozen_hash, "consumed_at": now().isoformat(), "policy_hash": report["promotion_policy_hash"]}
                db.execute("INSERT INTO records(kind,id,body,created_at) VALUES('holdout',?,?,?)", (holdout_key, canonical(record), now().isoformat()))
        if report["status"] == "holdout_already_consumed":
            return _save(store, report)
    report["audit"].append({"event": "holdout_opened", "at": now().isoformat(), "synthetic": synthetic})
    predictions = {name: model_predict(model, test) for name, model in models.items()}
    labels = [r["label"] for r in test]
    report["metrics"] = {name: metrics(labels, p) for name, p in predictions.items()}
    report["uncertainty"] = _bootstrap(test, predictions)
    report["subgroups"] = {}
    for field in ("niche", "account_size", "post_type", "language"):
        report["subgroups"][field] = {}
        for value in sorted({r[field] for r in test}):
            indices = [i for i, r in enumerate(test) if r[field] == value]
            group = [test[i] for i in indices]
            group_predictions = {name: p[indices] for name, p in predictions.items()}
            report["subgroups"][field][value] = {"metrics": {name: metrics([r["label"] for r in group], p) for name, p in group_predictions.items()},
                                                "uncertainty": _bootstrap(group, group_predictions, replicates=100)}
    heldout_indices = [i for i, r in enumerate(test) if r["author"] in manifest["heldout_authors"]]
    report["author_holdout"] = {"rows": len(heldout_indices), "authors": sorted({test[i]["author"] for i in heldout_indices}),
                                "metrics": {name: metrics([labels[i] for i in heldout_indices], p[heldout_indices]) for name, p in predictions.items()}}
    score_bands = []
    for band in range(1, 6):
        group = [r for r in test if min(5, max(1, math.floor(r["editorial"] + .5))) == band]
        score_bands.append({"editorial_band": band, "rows": len(group), "positives": sum(r["label"] for r in group),
                            "outcome_rate": sum(r["label"] for r in group) / len(group) if group else None})
    report["score_bands"] = score_bands
    report["failure_examples"] = [{"key": test[i]["key"], "observed_label": labels[i], "estimated_probability": float(predictions["jev_plus_metadata"][i]),
                                    "editorial_score": test[i]["editorial"], "partition": "test"}
                                   for i in np.argsort(-np.abs(np.array(labels) - predictions["jev_plus_metadata"]))[:10]]
    # Development-only residuals are separate from the test failures above.
    development_predictions = selection_prediction
    report["development_errors"] = [{"key": r["key"], "label": r["label"], "probability": float(p), "absolute_error": float(abs(r["label"] - p)),
                                      "partition": "selection"} for r, p in zip(selection, development_predictions)]
    report["status"] = "evaluated"
    report["predictive_validation"] = "not_established" if synthetic else "private_holdout_evaluated_promotion_pending"
    report["promotion"] = _promotion_gates(report)
    return _save(store, report)


def _promotion_gates(report: dict) -> dict:
    p = report["promotion_policy"]
    reasons = []
    def require(condition, reason):
        if not condition:
            reasons.append(reason)
    require(not report["synthetic"], "synthetic_evidence_cannot_promote")
    require(report["representative_sampling_attested"] and bool(report["comparison_population"].strip()), "representative_sampling_and_comparison_population_required")
    rows = report.get("development_rows", [])
    require(all(r.get("sampling_provenance", "unknown") not in ("manual", "unknown", "winners_only", "balanced") for r in rows), "sampling_provenance_must_be_documented")
    require(all(r["execution_mode"] == "live" and r["model_returned"] == r["model_requested"] for r in rows), "verified_live_pinned_model_required")
    require(report["eligibility"]["eligible_rows"] >= p["minimum_eligible_rows"], "minimum_eligible_rows")
    target, baseline = report["metrics"]["jev_plus_metadata"], report["metrics"]["metadata_only"]
    require(target["rows"] >= p["minimum_test_rows"], "minimum_test_rows")
    require(target["positives"] >= p["minimum_test_positives"], "minimum_test_positives")
    require(target["rows"] - target["positives"] >= p["minimum_test_negatives"], "minimum_test_negatives")
    require(report["uncertainty"]["clusters"] >= p["minimum_test_author_clusters"], "minimum_independent_author_clusters")
    require(target["expected_calibration_error"] <= p["maximum_expected_calibration_error"], "calibration_error")
    intervals = report["uncertainty"].get("intervals", {})
    upper = intervals.get("jev_plus_metadata.expected_calibration_error", {}).get("upper")
    require(upper is not None and upper <= p["maximum_calibration_gap_ci_upper"], "calibration_uncertainty")
    improvement = (baseline["brier"] - target["brier"]) / baseline["brier"] if baseline["brier"] else -1
    require(improvement >= p["minimum_relative_brier_improvement"], "incremental_brier_value_over_metadata")
    lower = intervals.get("paired_brier_improvement_vs_metadata", {}).get("lower")
    require(lower is not None and lower > 0, "positive_paired_cluster_improvement_interval")
    heldout = report["author_holdout"]["metrics"]["jev_plus_metadata"]
    require(heldout["rows"] >= p["minimum_author_holdout_rows"] and heldout["positives"] >= p["minimum_author_holdout_positives"]
            and heldout["rows"] - heldout["positives"] >= p["minimum_author_holdout_negatives"], "unfamiliar_author_support")
    heldout_base = report["author_holdout"]["metrics"]["metadata_only"]
    require(heldout.get("brier") is not None and heldout_base.get("brier") is not None and heldout["brier"] < heldout_base["brier"], "unfamiliar_author_incremental_value")
    return {"eligible": not reasons, "reasons": reasons, "manual_approval_required": True,
            "approved": False, "rationale": p["rationale"]}


def promote(store: Store, experiment_id: str, *, approved_by: str, rationale: str) -> dict:
    report = store.get("experiment", experiment_id)
    if not report:
        raise ValueError("Experiment does not exist")
    if not approved_by.strip() or not rationale.strip():
        raise ValueError("Explicit approving identity and rationale are required")
    gates = _promotion_gates(report) if report.get("status") == "evaluated" else {"eligible": False, "reasons": ["experiment_not_evaluated"]}
    decision = {"event": "manual_promotion_decision", "at": now().isoformat(), "approved_by": approved_by,
                "rationale": rationale, "accepted": bool(gates["eligible"]), "gate_failures": gates["reasons"]}
    report["audit"].append(decision)
    report["promotion"] = dict(gates, approved=decision["accepted"], approval=decision)
    if decision["accepted"]:
        first = report["development_rows"][0]
        predictor = {"predictor_id": experiment_id, "model": report["models"]["jev_plus_metadata"], "task": report["task"],
                     "tier_boundaries": report["tier_boundaries"], "comparison_population": report["comparison_population"],
                     "configuration": {k: first[k] for k in ("profile_id", "audience_id", "audience_version", "rubric_version", "model_requested")},
                     "approval": decision, "frozen_candidate_hash": report["frozen_candidate_hash"]}
        store.put("predictor", experiment_id, predictor)
        report["forecast_available"] = True
    return _save(store, report)


def predict(store: Store, judgment_id: str, *, predictor_id: str | None = None) -> dict:
    predictors = store.list("predictor")
    predictor = store.get("predictor", predictor_id) if predictor_id else (predictors[-1] if predictors else None)
    unavailable = {"mode": "forecast", "available": False, "breakout_probability": None, "score_1_to_5": None, "calibration_status": "not_established"}
    if not predictor or not predictor.get("approval", {}).get("accepted"):
        return dict(unavailable, reason="No evidence-qualified, manually approved forecast predictor")
    judgment = store.get("judgment", judgment_id)
    if not judgment:
        raise ValueError("Judgment does not exist")
    if judgment.get("status") != "scored" or judgment.get("execution_mode") != "live":
        return dict(unavailable, reason="Forecast requires a complete live judgment")
    if judgment.get("model_returned") != judgment.get("model_requested"):
        return dict(unavailable, reason="Forecast requires the verified pinned model identity")
    if any(judgment.get(k) != v for k, v in predictor["configuration"].items()):
        return dict(unavailable, reason="Judgment profile, audience, rubric or pinned model differs from the evaluated predictor")
    runs = [r for r in store.list("run") if r.get("judgment_id") == judgment_id]
    if not runs:
        return dict(unavailable, reason="Prediction context is unavailable")
    request = runs[0]["request"]
    candidate, context = request["candidate"], request["context"]
    if candidate.get("synthetic") or candidate.get("distribution") != "organic":
        return dict(unavailable, reason="Forecast population requires real candidates with explicitly organic distribution")
    if any(not _finite(judgment.get("factors", {}).get(name, {}).get("score")) for name in SEMANTIC_NAMES):
        return dict(unavailable, reason="Forecast feature evidence is incomplete")
    # Baseline for inference must not need the target outcome. Supply a temporary
    # same-source placeholder only to reconstruct history, then discard its label.
    sources = {o["source"] for o in store.list("outcome") if o.get("metric") == "views" and o.get("distribution") == "organic"}
    if len(sources) != 1:
        return dict(unavailable, reason="Forecast baseline requires one explicit comparable views source")
    cutoff = _date(context["prediction_cutoff"])
    target = dict(candidate, published_at=candidate.get("published_at") or cutoff.isoformat(), distribution="organic")
    publication = _date(target["published_at"])
    temporary = {"candidate_id": target["candidate_id"], "candidate_version": target["candidate_version"], "observation_id": "forecast-placeholder-never-persisted",
                 "source": next(iter(sources)), "metric": "views", "distribution": "organic", "views": 0, "synthetic": False,
                 "elapsed_hours": 48, "observed_at": (publication + timedelta(hours=48)).isoformat(), "available_at": (publication + timedelta(hours=48)).isoformat()}
    outcomes = [o for o in store.list("outcome") if _key(o) != _key(target)] + [temporary]
    label = label_candidate(target, context, store.list("candidate"), outcomes, task=predictor["task"], as_of=publication + timedelta(hours=49))
    if label["status"] != "eligible":
        return dict(unavailable, reason=f"Forecast baseline unavailable: {label['reason']}")
    row = _feature_row(candidate, context, judgment, label)
    probability = float(model_predict(predictor["model"], [row])[0])
    tier = 1 + sum(probability >= t for t in predictor["tier_boundaries"])
    return {"mode": "forecast", "available": True, "breakout_probability": probability, "score_1_to_5": tier,
            "predictor_id": predictor["predictor_id"], "label_definition_id": predictor["task"], "calibration_status": "heldout_evaluated_manually_approved",
            "comparison_population": predictor["comparison_population"], "tier_boundaries": predictor["tier_boundaries"],
            "editorial_score_1_to_5": judgment.get("score_1_to_5"), "judgment_id": judgment_id}


def forecast_status(store: Store) -> dict:
    predictors = [p for p in store.list("predictor") if p.get("approval", {}).get("accepted")]
    if predictors:
        p = predictors[-1]
        return {"available": True, "predictor_id": p["predictor_id"], "label_definition_id": p["task"],
                "comparison_population": p["comparison_population"], "reasons": [], "manual_approval": p["approval"]}
    experiments = store.list("experiment")
    reasons = experiments[-1].get("promotion", {}).get("reasons", []) if experiments else []
    return {"available": False, "reasons": reasons or ["No outcome model has passed an untouched real-data evaluation and explicit manual approval"],
            "breakout_probability": None, "calibration_status": "not_established"}


def compare_experiments(store: Store, experiment_ids: list[str]) -> dict:
    """Inspect version/ablation reports without fitting or reopening any holdout."""
    if not 2 <= len(experiment_ids) <= 8 or len(set(experiment_ids)) != len(experiment_ids):
        raise ValueError("Compare two to eight distinct persisted experiments")
    reports = [store.get("experiment", identity) for identity in experiment_ids]
    if any(r is None for r in reports):
        raise ValueError("An experiment does not exist")
    test_sets = [set(r.get("split_manifest", {}).get("partitions", {}).get("test", [])) for r in reports]
    same_keys = all(s == test_sets[0] for s in test_sets)
    same_task = len({r["task"] for r in reports}) == 1
    same_mode = len({r["synthetic"] for r in reports}) == 1
    reasons = []
    if not same_keys:
        reasons.append("Different final cohorts: metric differences cannot establish rubric improvement")
    if not same_task:
        reasons.append("Different outcome definitions")
    if not same_mode:
        reasons.append("Synthetic and real evidence cannot establish comparative performance")
    return {"experiment_ids": experiment_ids, "same_test_cohort": same_keys,
            "descriptively_comparable": same_keys and same_task and same_mode, "limitations": reasons,
            "causal_improvement_established": False, "test_reopened": False,
            "experiments": [{"experiment_id": r["experiment_id"], "task": r["task"], "synthetic": r["synthetic"],
                             "cohort_filter": r.get("cohort_filter", {}), "feature_schema": r["feature_schema"],
                             "configuration_signatures": r["eligibility"].get("configuration_signatures", []),
                             "metrics": r.get("metrics", {}), "promotion": r.get("promotion", {})} for r in reports]}
