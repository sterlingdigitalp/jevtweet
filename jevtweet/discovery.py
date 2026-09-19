"""Bounded, reviewed feature research using development partitions exclusively.

The injected callback is the existing Jev runtime with its own account-wide cost
controls. This module adds experiment limits and never publishes a predictor.
"""
from __future__ import annotations

from copy import deepcopy
import inspect
import math
import re
from typing import Awaitable, Callable

import numpy as np
from sklearn.metrics import log_loss

from .contracts import canonical, digest, now, uid
from .evaluation import CONFIG, SEMANTIC_NAMES, METADATA_NAMES, _date, _finite, _sigmoid, fit_model, raw_predict
from .storage import Store

FeatureProvider = Callable[[dict, dict], Awaitable[dict]]


def _experiment(store: Store, experiment_id: str) -> dict:
    experiment = store.get("experiment", experiment_id)
    if not experiment:
        raise ValueError("Experiment does not exist")
    if not experiment.get("development_partitions"):
        raise ValueError("Experiment lacks frozen development partitions")
    return experiment


def development_errors(store: Store, experiment_id: str) -> dict:
    """Narrow proposal-agent view; test metrics/rows/errors are never returned."""
    experiment = _experiment(store, experiment_id)
    allowed = {r["key"]: r for r in experiment["development_partitions"].get("selection", [])}
    records = []
    for error in experiment.get("development_errors", []):
        if error["key"] not in allowed or error.get("partition") != "selection":
            raise ValueError("Development view contains a non-development key")
        row = allowed[error["key"]]
        records.append(dict(error, candidate_text=row["text"], factors={k: row["features"].get(k) for k in SEMANTIC_NAMES}))
    return {"experiment_id": experiment_id, "synthetic": experiment["synthetic"], "partition": "selection",
            "errors": sorted(records, key=lambda e: -e["absolute_error"]),
            "limitations": "Development-only error analysis; repeated research can overfit this partition. Final test is excluded and cannot be reopened."}


def validate_proposals(proposals: list[dict]) -> list[dict]:
    policy = CONFIG["discovery"]
    if not isinstance(proposals, list) or not 1 <= len(proposals) <= policy["maximum_proposals"]:
        raise ValueError(f"Supply one to {policy['maximum_proposals']} reviewed feature proposals")
    normalized, seen = [], set()
    permitted = {"feature_id", "version", "type", "instructions", "criteria", "reviewed_by", "reviewed_at", "hypothesis"}
    for raw in proposals:
        if not isinstance(raw, dict) or set(raw) - permitted:
            raise ValueError("Proposal contains unsupported fields")
        required = permitted - {"criteria"}
        if required - set(raw):
            raise ValueError(f"Proposal missing fields: {', '.join(sorted(required - set(raw)))}")
        proposal = deepcopy(raw)
        feature_id = proposal["feature_id"]
        if not isinstance(feature_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", feature_id) or feature_id in seen or feature_id in SEMANTIC_NAMES + METADATA_NAMES:
            raise ValueError("Feature IDs must be unique descriptive snake_case, outside the existing schema")
        seen.add(feature_id)
        if proposal["type"] not in ("score", "noul"):
            raise ValueError("Discovery supports narrow Score or Noul questions only")
        if not all(isinstance(proposal[k], str) and proposal[k].strip() for k in ("version", "instructions", "reviewed_by", "reviewed_at", "hypothesis")):
            raise ValueError("Proposal version, instructions, human review and hypothesis are required")
        if _date(proposal["reviewed_at"]) > now():
            raise ValueError("Proposal review timestamp is in the future")
        if len(proposal["instructions"]) > 3000 or len(proposal["hypothesis"]) > 2000:
            raise ValueError("Proposal exceeds narrow question limits")
        instructions = proposal["instructions"].lower()
        forbidden = ("future outcomes", "observed views", "engagement counts", "viral label", "target label", "test set", "test partition", "private behavior", "production ranking weight")
        if any(term in instructions for term in forbidden):
            raise ValueError("Question requests forbidden outcome/test/private-behavior information")
        if proposal["type"] == "score":
            criteria = proposal.get("criteria")
            if not isinstance(criteria, list) or len(criteria) != 5 or any(not isinstance(c, str) or not c.strip() or len(c) > 800 for c in criteria) or len(set(criteria)) != 5:
                raise ValueError("Score proposals require five distinct ordered descriptive criteria")
        elif proposal.get("criteria"):
            raise ValueError("Noul does not accept Score criteria")
        normalized.append(proposal)
    return normalized


def _safe_state(row: dict) -> dict:
    state = row.get("state")
    if not isinstance(state, dict):
        # The built-in fixture has no Jev request; a synthetic text-only state
        # allows deterministic mock callbacks to exercise the research wiring.
        if not row.get("synthetic"):
            raise ValueError("Missing prediction-time-safe run state")
        return {"candidate": {"text": row["text"], "language": row["language"], "post_type": row["post_type"]},
                "audience": {"audience_id": row.get("audience_id"), "version": row["audience_version"]},
                "prediction_cutoff": row["cutoff"], "synthetic": True}
    allowed = {"candidate", "audience", "prediction_cutoff", "topic", "references", "evidence", "preprocessing_version"}
    answer = {k: deepcopy(v) for k, v in state.items() if k in allowed}
    forbidden = {"outcome", "outcomes", "views", "impressions", "likes", "reposts", "replies", "label", "labels", "filename", "provenance"}
    def check(obj):
        if isinstance(obj, dict):
            if set(obj) & forbidden:
                raise ValueError("Stored run state has forbidden feature-discovery fields")
            if obj.get("available_at") and _date(obj["available_at"]) > _date(row["cutoff"]):
                raise ValueError("Stored run state contains future context")
            for value in obj.values():
                check(value)
        elif isinstance(obj, list):
            for value in obj:
                check(value)
    check(answer)
    return answer


def _persist(store: Store, result: dict):
    store.put("discovery", result["discovery_id"], result, replace=True)


async def discover(store: Store, experiment_id: str, proposals: list[dict], *, feature_provider: FeatureProvider,
                   cost_limit_usd: float, cost_estimate_per_request_usd: float,
                   max_rows: int = 200, max_requests: int = 400, iteration: int | None = None) -> dict:
    experiment = _experiment(store, experiment_id)
    proposals = validate_proposals(proposals)
    if not _finite(cost_limit_usd) or cost_limit_usd <= 0 or not _finite(cost_estimate_per_request_usd) or cost_estimate_per_request_usd <= 0:
        raise ValueError("Discovery requires explicit positive finite total cost and conservative per-request estimates")
    if not 24 <= max_rows <= 1000 or not 1 <= max_requests <= 8000:
        raise ValueError("Discovery row limit must be 24–1000 and request limit 1–8000")
    history = [r for r in store.list("discovery") if r["experiment_id"] == experiment_id]
    next_iteration = len(history) + 1
    if next_iteration > CONFIG["discovery"]["maximum_iterations"] or (iteration is not None and iteration != next_iteration):
        raise ValueError("Discovery iteration is out of sequence or exceeds the five-iteration cap")
    if history and any(r["cost_limit_usd"] != cost_limit_usd for r in history):
        raise ValueError("An experiment's research cost ceiling is frozen by its first iteration")
    if history and any(r.get("max_rows", max_rows) != max_rows for r in history):
        raise ValueError("Keep the same bounded development row cohort across discovery iterations")
    active = [f for r in history for f in r.get("features", []) if f.get("accepted")]
    active_ids = {f["feature_id"] for f in active}
    if any(p["feature_id"] in active_ids for p in proposals):
        raise ValueError("A proposed feature is already active")
    if len(SEMANTIC_NAMES) + len(active) + len(proposals) > CONFIG["discovery"]["maximum_active_semantic_features"]:
        raise ValueError("Proposal would exceed the 24 active semantic feature ceiling")
    partitions = experiment["development_partitions"]
    train, selection = deepcopy(partitions["train"]), deepcopy(partitions["selection"])
    # A bounded chronological subset within each allowed development partition.
    selection_limit = min(len(selection), max(12, int(max_rows * .3)))
    train = train[-(max_rows - selection_limit):]
    selection = selection[:selection_limit]
    if len(train) < 12 or len(selection) < 12 or len({r["label"] for r in train}) < 2 or len({r["label"] for r in selection}) < 2:
        raise ValueError("Discovery needs both classes in at least 12 train and 12 selection rows")
    rows = train + selection
    allowed_keys = set(experiment["split_manifest"]["partitions"]["train"] + experiment["split_manifest"]["partitions"]["selection"])
    test_keys = set(experiment["split_manifest"]["partitions"]["test"])
    if any(r["key"] not in allowed_keys or r["key"] in test_keys for r in rows):
        raise ValueError("Forbidden partition contamination in feature discovery")
    for row in rows:
        _safe_state(row)
    required = len(rows) * len(proposals)
    charged_before = sum(r.get("charged_or_reserved_usd", 0) for r in history)
    if required > max_requests or charged_before + required * cost_estimate_per_request_usd > cost_limit_usd + 1e-12:
        raise ValueError("Proposed iteration exceeds explicit request or cumulative cost ceiling")
    result = {"discovery_id": uid(), "experiment_id": experiment_id, "iteration": next_iteration,
              "created_at": now().isoformat(), "status": "running", "synthetic": experiment["synthetic"],
              "policy_version": CONFIG["version"], "policy": CONFIG["discovery"], "cost_limit_usd": cost_limit_usd,
              "cost_estimate_per_request_usd": cost_estimate_per_request_usd, "max_rows": max_rows, "max_requests": max_requests,
              "charged_or_reserved_usd": 0.0, "request_count": 0, "proposals": proposals, "features": [], "audit": [],
              "development_keys": [r["key"] for r in rows], "test_exposed": False,
              "production_promoted": False, "holdout_reuse_authorized": False,
              "limitations": ["Accepted features are development research only. A new untouched holdout and manual approval are required for production.",
                              "Reusing selection errors across iterations can overfit development data."]}
    # Serialize iteration creation so concurrent requests cannot each observe
    # the same remaining experiment budget or create a sixth iteration.
    with store.transaction() as db:
        import json
        existing = [json.loads(r[0]) for r in db.execute("SELECT body FROM records WHERE kind='discovery'").fetchall()]
        matching = [r for r in existing if r["experiment_id"] == experiment_id]
        if len(matching) != next_iteration - 1 or any(r.get("status") == "running" for r in matching):
            raise ValueError("Another discovery iteration is running or changed the experiment budget; inspect or cancel it before continuing")
        db.execute("INSERT INTO records(kind,id,body,created_at) VALUES('discovery',?,?,?)",
                   (result["discovery_id"], canonical(result), now().isoformat()))
    names = SEMANTIC_NAMES + METADATA_NAMES
    # Retained features enter subsequent comparisons, so redundant rediscoveries
    # must improve the complete current feature set rather than the original base.
    for prior in active:
        prior_values = prior.get("values", {})
        names.append(prior["feature_id"])
        for row in rows:
            row["features"][prior["feature_id"]] = prior_values.get(row["key"])
    base_model = fit_model(train, names, 1)
    base_loss = float(log_loss([r["label"] for r in selection], _sigmoid(raw_predict(base_model, selection)), labels=[0, 1]))
    result["baseline_selection_log_loss"] = base_loss
    for proposal in proposals:
        feature_id = proposal["feature_id"]
        feature = {"feature_id": feature_id, "version": proposal["version"], "proposal_hash": digest(proposal),
                   "accepted": False, "values": {}, "errors": [], "decision_reasons": []}
        result["features"].append(feature)
        question = {"question_id": feature_id, "type": proposal["type"], "instructions": proposal["instructions"]}
        if proposal["type"] == "score":
            question["criteria"] = proposal["criteria"]
        for row in rows:
            estimate = cost_estimate_per_request_usd
            if charged_before + result["charged_or_reserved_usd"] + estimate > cost_limit_usd + 1e-12 or result["request_count"] >= max_requests:
                result["status"] = "budget_stopped"
                result["audit"].append({"event": "budget_stop", "at": now().isoformat()})
                _persist(store, result)
                return result
            result["charged_or_reserved_usd"] += estimate
            result["request_count"] += 1
            _persist(store, result)  # Uncertain/crashed requests retain a reservation.
            try:
                response = feature_provider(_safe_state(row), deepcopy(question))
                if inspect.isawaitable(response):
                    response = await response
                if not isinstance(response, dict):
                    raise ValueError("Feature provider must return a structured response")
                cost = response.get("estimated_cost_usd")
                if not _finite(cost) or cost < 0:
                    raise ValueError("Feature provider must account for finite nonnegative cost")
                result["charged_or_reserved_usd"] += cost - estimate
                if cost > estimate + 1e-12:
                    result["status"] = "provider_estimate_exceeded"
                    result["audit"].append({"event": "provider_cost_exceeded_reservation", "estimate": estimate, "reported": cost})
                    _persist(store, result)
                    return result
                expected = "mock" if experiment["synthetic"] else "live"
                if response.get("execution_mode") != expected:
                    raise ValueError("Live/mock feature mode cannot mix with the evaluated cohort")
                value = response.get("value")
                maximum = 4 if proposal["type"] == "score" else 1
                if response.get("status") != "ok" or not _finite(value) or not 0 <= value <= maximum:
                    raise ValueError(response.get("error") or "Feature answer unavailable or outside primitive range")
                feature["values"][row["key"]] = float(value)
            except Exception as error:
                feature["errors"].append({"key": row["key"], "error": str(error)[:500]})
            _persist(store, result)
        complete = len(feature["values"]) / len(rows)
        feature["coverage"] = complete
        if complete < CONFIG["discovery"]["minimum_complete_fraction"]:
            feature["decision_reasons"].append("insufficient_feature_coverage")
        # Same cohort for candidate and baseline; missing outputs remain missing.
        for row in rows:
            row["features"][feature_id] = feature["values"].get(row["key"])
        correlations = {}
        for existing in names:
            pairs = [(r["features"].get(existing), r["features"].get(feature_id)) for r in train]
            pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
            if len(pairs) > 2:
                a, b = np.array(pairs).T
                correlations[existing] = float(abs(np.corrcoef(a, b)[0, 1])) if np.std(a) > 0 and np.std(b) > 0 else 1.0 if np.std(b) == 0 else 0.0
        maximum = max(correlations.values(), default=1.0)
        feature["maximum_train_correlation"] = maximum
        feature["train_correlations"] = correlations
        if maximum >= CONFIG["discovery"]["maximum_absolute_correlation"]:
            feature["decision_reasons"].append("redundant_or_constant_feature")
        candidate_model = fit_model(train, names + [feature_id], 1)
        candidate_loss = float(log_loss([r["label"] for r in selection], _sigmoid(raw_predict(candidate_model, selection)), labels=[0, 1]))
        feature["baseline_selection_log_loss"] = base_loss
        feature["candidate_selection_log_loss"] = candidate_loss
        feature["log_loss_improvement"] = base_loss - candidate_loss
        if base_loss - candidate_loss < CONFIG["discovery"]["minimum_log_loss_improvement"]:
            feature["decision_reasons"].append("insufficient_incremental_development_value")
        feature["accepted"] = not feature["decision_reasons"]
        feature["fitting_partition"] = "train"
        feature["decision_partition"] = "selection"
        if feature["accepted"]:
            names.append(feature_id)
            base_loss = candidate_loss
        result["audit"].append({"event": "feature_decision", "feature_id": feature_id, "accepted": feature["accepted"],
                                "reasons": feature["decision_reasons"], "at": now().isoformat()})
        _persist(store, result)
    result["status"] = "complete"
    result["active_semantic_features"] = len(SEMANTIC_NAMES) + len(active) + sum(f["accepted"] for f in result["features"])
    _persist(store, result)
    return result
