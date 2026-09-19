"""Development-only planning and an explicit, immutable final-test opening.

Prediction inputs determine chronological membership before any outcome is read.
Only opening a frozen candidate may read the protected observations. This module
contains workflow orchestration; the evaluator owns fitting, metrics and gates.
"""

from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from datetime import timedelta

from . import evaluation as ev
from .contracts import canonical, digest, now, uid
from .storage import Store

WORKFLOW_VERSION = "holdout_workflow_v1"
REQUIRED_COHORT = (
    "audience_id",
    "audience_version",
    "profile_id",
    "rubric_version",
    "model_requested",
    "outcome_source",
)


def _configuration_errors(
    *, synthetic, task, representative_sampling, comparison_population, sampling_declaration, cohort, max_rows
):
    if task not in ev.CONFIG["label_definitions"]:
        raise ValueError("Unknown label definition")
    if not 40 <= max_rows <= 5000:
        raise ValueError("Evaluation max_rows must be between 40 and 5000")
    if set(cohort or {}) - ev.COHORT_FIELDS:
        raise ValueError("Unknown cohort filter; use explicit judgment configuration fields")
    if synthetic:
        return []
    errors = []
    for field in REQUIRED_COHORT:
        if not isinstance((cohort or {}).get(field), str) or not cohort[field].strip():
            errors.append(f"Explicit cohort.{field} is required before freezing or opening a real holdout")
    if not representative_sampling:
        errors.append("Representative sampling must be explicitly attested before freezing a real holdout")
    if not comparison_population.strip():
        errors.append("An explicit comparison population is required before freezing a real holdout")
    if not sampling_declaration.strip():
        errors.append("A written sampling declaration is required before freezing a real holdout")
    if (cohort or {}).get("execution_mode", "live") != "live":
        errors.append("A real evaluation requires live execution mode")
    if (cohort or {}).get("profile_id") not in ev.RUBRIC["profiles"]:
        errors.append("The profile must match the versioned rubric")
    if (cohort or {}).get("rubric_version") != ev.RUBRIC["version"]:
        errors.append("The rubric must match the currently verified rubric")
    if (cohort or {}).get("model_requested") != ev.Settings.model:
        errors.append("The requested model must match the verified pinned model")
    return errors


def _protected_identities(store):
    identities = [
        identity for record in store.list("holdout") for identity in record.get("test_identities", [])
    ]
    keys = {key for record in store.list("holdout") for key in record.get("test_keys", [])}
    for record in store.list("evaluation_freeze"):
        identities.extend(record.get("protected_test_identities", []))
        keys.update(record.get("protected_test_keys", []))
    return identities, keys


def _unlabeled_row(entry, source):
    candidate, judgment, run = entry["candidate"], entry["judgment"], entry["run"]
    label = {
        "label": 0,
        "source": source,
        "baseline_views": None,
        "baseline_sample_count": 0,
        "label_available_at": (
            (
                ev._date(candidate.get("published_at") or run["request"]["context"]["prediction_cutoff"])
                + timedelta(hours=49)
            ).isoformat()
        ),
    }
    return ev._feature_row(candidate, run["request"]["context"], judgment, label, run.get("state"))


def _exposure_conflicts(db, identities):
    exposed = [
        json.loads(row[0])["identity"]
        for row in db.execute("SELECT body FROM records WHERE kind='development_exposure'").fetchall()
    ]
    # Preserve historical exposure from reports written before this workflow;
    # old declarations are never rewritten or silently upgraded.
    legacy_keys = set()
    for record in db.execute("SELECT body FROM records WHERE kind='experiment'").fetchall():
        report = json.loads(record[0])
        if report.get("synthetic"):
            continue
        for row in report.get("development_rows", []):
            exposed.append(ev._holdout_identity(row))
        legacy_keys.update(
            row["key"] for row in report.get("eligibility", {}).get("labels", []) if row.get("key")
        )
    if legacy_keys:
        for record in db.execute("SELECT body FROM records WHERE kind='candidate'").fetchall():
            candidate = json.loads(record[0])
            if ev._key(candidate) in legacy_keys:
                exposed.append(ev._holdout_identity(dict(candidate, key=ev._key(candidate))))
    return ev._overlaps_holdout(identities, exposed)


def _safe_outcomes(store, protected_ids, *, authorized_test_ids=None, record_exposure=False):
    """Read bodies only after atomic protection and exposure bookkeeping.

    A preflight writes no experiment or holdout, but remembers which development
    or historical observations were accessible. Their identities can never be
    relabeled as an untouched final test by changing a later corpus boundary.
    """
    authorized_test_ids = set(authorized_test_ids or ())
    with store.transaction() as db:
        protected_ids = set(protected_ids)
        current = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT body FROM records WHERE kind IN ('holdout', 'evaluation_freeze')"
            ).fetchall()
        ]
        identities = [
            identity
            for record in current
            for identity in record.get("test_identities", record.get("protected_test_identities", []))
        ]
        keys = {
            key
            for record in current
            for key in record.get("test_keys", record.get("protected_test_keys", []))
        }
        protected_ids.update(key.rsplit(":", 1)[0] for key in keys)
        candidates = [
            json.loads(row[0])
            for row in db.execute("SELECT body FROM records WHERE kind='candidate'").fetchall()
        ]
        by_key = {ev._key(candidate): candidate for candidate in candidates}
        for candidate in candidates:
            identity = ev._holdout_identity(dict(candidate, key=ev._key(candidate)))
            if ev._overlaps_holdout([identity], identities):
                protected_ids.add(candidate["candidate_id"])
        protected_ids -= authorized_test_ids
        clause = (
            " AND json_extract(body, '$.candidate_id') NOT IN (" + ",".join("?" for _ in protected_ids) + ")"
            if protected_ids
            else ""
        )
        parameters = sorted(protected_ids)
        if record_exposure:
            observed = db.execute(
                "SELECT DISTINCT json_extract(body, '$.candidate_id'), COALESCE(json_extract(body, '$.candidate_version'),1) FROM records WHERE kind='outcome'"
                + clause,
                parameters,
            ).fetchall()
            for candidate_id, version in observed:
                key = f"{candidate_id}:{version}"
                candidate = by_key.get(key)
                identity = (
                    ev._holdout_identity(dict(candidate, key=key))
                    if candidate
                    else {"key": key, "candidate_id": candidate_id, "tokens": []}
                )
                exposure = {
                    "identity": identity,
                    "first_exposed_at": now().isoformat(),
                    "scope": "development_or_history",
                }
                db.execute(
                    "INSERT OR IGNORE INTO records(kind,id,body,created_at) VALUES('development_exposure',?,?,?)",
                    (digest(key), canonical(exposure), now().isoformat()),
                )
        records = db.execute("SELECT body FROM records WHERE kind='outcome'" + clause, parameters).fetchall()
    return [json.loads(record[0]) for record in records]


def prepare_development(store: Store, *, task="breakout_48h_v1", synthetic=False, max_rows=5000, cohort=None):
    """Return only development labels; test membership precedes all label access.

    The 5,000-row cap applies to the intended prediction cohort before attempt or label
    exclusions. Exceeding it requires a narrower predeclared collection cohort;
    rows are never silently selected by observed outcomes.
    """
    if task not in ev.CONFIG["label_definitions"]:
        raise ValueError("Unknown label definition")
    if not 40 <= max_rows <= 5000:
        raise ValueError("Evaluation max_rows must be between 40 and 5000")
    cohort = cohort or {}
    selection = ev._select_judgments(store, synthetic=synthetic, cohort=cohort)
    entries = selection.pop("selected")
    candidates = selection.pop("candidates")
    prior_identities, prior_keys = _protected_identities(store)
    prior_ids = {key.rsplit(":", 1)[0] for key in prior_keys}
    protected_ids = set(prior_ids)
    for candidate in candidates:
        identity = ev._holdout_identity(dict(candidate, key=ev._key(candidate)))
        if (
            ev._key(candidate) in prior_keys
            or candidate["candidate_id"] in prior_ids
            or ev._overlaps_holdout([identity], prior_identities)
        ):
            protected_ids.add(candidate["candidate_id"])
    kept = []
    for entry in entries:
        if entry["candidate"]["candidate_id"] in protected_ids:
            selection["exclusions"].append(
                {
                    "key": ev._key(entry["candidate"]),
                    "reason": "previously_frozen_or_opened_holdout_protected",
                }
            )
        else:
            kept.append(entry)
    entries = kept
    fixture_used = synthetic and not entries and not any(bool(c.get("synthetic")) for c in candidates)
    fixture_rows = ev._synthetic_rows(min(400, max_rows)) if fixture_used else []
    entries_by_key = {ev._key(entry["candidate"]): entry for entry in entries}
    attempts_by_key = {}
    for attempt in selection["judgment_attempts"]:
        attempts_by_key.setdefault(attempt["key"], []).append(attempt)
    rows = []
    unassignable = 0
    if fixture_used:
        rows = [dict(row, label=0) for row in fixture_rows]
    else:
        for candidate in candidates:
            key = ev._key(candidate)
            if bool(candidate.get("synthetic")) != synthetic or candidate["candidate_id"] in protected_ids:
                continue
            attempts = attempts_by_key.get(key, [])
            if attempts and all(a["reason"] == "outside_explicit_judgment_configuration" for a in attempts):
                continue
            if key in entries_by_key:
                rows.append(_unlabeled_row(entries_by_key[key], cohort.get("outcome_source")))
                continue
            cutoff = candidate.get("published_at") or candidate.get("content_available_at")
            if not cutoff:
                unassignable += 1
                continue
            # Missing and unsuccessful predictions remain in the intended
            # sampling denominator and temporal assignment before exclusions.
            rows.append(
                {
                    "key": key,
                    "candidate_id": candidate["candidate_id"],
                    "text": candidate["text"],
                    "cutoff": cutoff,
                    "label_available_at": (ev._date(cutoff) + timedelta(hours=49)).isoformat(),
                    "label": 0,
                    "author": candidate.get("author_id") or f"unknown:{key}",
                    "author_known": bool(candidate.get("author_id")),
                    "thread_id": candidate.get("thread_id"),
                    "references": [],
                    "sampling_provenance": candidate.get("provenance", "unknown"),
                }
            )
    if len(rows) > max_rows:
        raise ValueError(
            f"Prediction cohort exceeds explicit row limit {max_rows}; narrow the predeclared corpus before reading labels"
        )
    ordered = sorted(rows, key=lambda r: (ev._date(r["cutoff"]), r["key"]))
    test_start = int(len(ordered) * sum(ev.CONFIG["split_fractions"][name] for name in ev.SPLITS[:-1]))
    raw_test = ordered[test_start:]
    raw_development = ordered[:test_start]
    raw_test_identities = [ev._holdout_identity(row) for row in raw_test]
    test_keys = {row["key"] for row in raw_test}
    protected_ids.update(row["candidate_id"] for row in raw_test)
    # All originally assigned final-test rows remain protected even when a
    # duplicate/thread/reference purge later removes them from metric coverage.
    for candidate in candidates:
        if ev._overlaps_holdout(
            [ev._holdout_identity(dict(candidate, key=ev._key(candidate)))], raw_test_identities
        ):
            protected_ids.add(candidate["candidate_id"])
    partitions, manifest = ev.split_dataset(rows)
    for name in ev.SPLITS:
        manifest["counts"][name]["positives"] = None
    manifest["policy"] = "prediction_inputs_before_labels_v1; " + manifest["policy"]
    manifest["raw_test_keys"] = sorted(test_keys)
    development_keys = {key for name in ev.SPLITS[:-1] for key in manifest["partitions"][name]}
    for key in development_keys:
        if key.rsplit(":", 1)[0] in protected_ids:
            selection["exclusions"].append(
                {"key": key, "reason": "protected_holdout_alias_excluded_from_development"}
            )
    development_keys = {key for key in development_keys if key.rsplit(":", 1)[0] not in protected_ids}
    selection["exclusions"].extend(manifest["removals"])
    eligible_test_inputs = [row for row in partitions["test"] if fixture_used or row["key"] in entries_by_key]
    for row in eligible_test_inputs:
        row["label"] = None
        row["label_available_at"] = None
        if "label_details" in row:
            row["label_details"] = dict(
                row["label_details"], label=None, label_available_at=None, status="withheld"
            )
    outcomes = _safe_outcomes(store, protected_ids, record_exposure=not synthetic) if not fixture_used else []
    if fixture_used:
        by_key = {r["key"]: r for r in fixture_rows}
        labeled = [by_key[key] for key in development_keys]
        labels, rejected = [], []
    else:
        labeled, labels, rejected = ev._label_selected(
            [entries_by_key[key] for key in development_keys if key in entries_by_key],
            candidates,
            outcomes,
            task=task,
            outcome_source=cohort.get("outcome_source"),
        )
    selection["exclusions"].extend(rejected)
    labeled, signatures = ev._coherence(labeled, selection["exclusions"])
    by_key = {r["key"]: r for r in labeled}
    development_partitions = {}
    for index, name in enumerate(ev.SPLITS[:-1]):
        boundary = manifest["boundaries"][ev.SPLITS[index + 1]]
        group = []
        for original in partitions[name]:
            row = by_key.get(original["key"])
            if row is None:
                continue
            if boundary and ev._date(row["label_available_at"]) > ev._date(boundary):
                selection["exclusions"].append(
                    {"key": row["key"], "reason": "not_mature_at_fixed_next_boundary", "split": name}
                )
                continue
            group.append(dict(row, cluster=original["cluster"]))
        development_partitions[name] = group
        # Membership is fixed before labels; retain the intended manifest and
        # show eligible members separately rather than shifting split boundaries.
        manifest.setdefault("eligible_partitions", {})[name] = [r["key"] for r in group]
        manifest["counts"][name] = {"rows": len(group), "positives": sum(r["label"] for r in group)}
    development = [r for name in ev.SPLITS[:-1] for r in development_partitions[name]]
    development_count = len(raw_development) + unassignable
    with store.connect() as db:
        previously_exposed_test = not synthetic and _exposure_conflicts(db, raw_test_identities)
    return dict(
        selection,
        rows=development,
        labels=labels,
        total_candidates=len(candidates) if not fixture_used else len(rows),
        development_candidate_count=development_count,
        previously_exposed_test=previously_exposed_test,
        coverage=len(development) / development_count if development_count else None,
        development_partitions=development_partitions,
        split_manifest=manifest,
        protected_test_keys=sorted(test_keys),
        protected_test_identities=raw_test_identities,
        test_input_rows=eligible_test_inputs,
        test_entries=[entries_by_key[row["key"]] for row in eligible_test_inputs] if not fixture_used else [],
        excluded_outcome_candidate_ids=sorted(protected_ids),
        collection_counts={
            "intended_candidates": len(rows) + unassignable,
            "qualified_prediction_count": len(entries),
            "intended_development": development_count,
            "unassignable_candidates": unassignable,
            "development_after_structural_purges": len(development_keys),
            "development_with_qualifying_judgment": sum(key in entries_by_key for key in development_keys),
            "development_labeled_rows": len(development),
        },
        candidate_records=candidates,
        fixture_test_rows=[
            r for r in fixture_rows if r["key"] in {item["key"] for item in eligible_test_inputs}
        ],
        fixture_used=fixture_used,
        configuration_signatures=signatures,
        exclusion_counts=dict(Counter(e["reason"] for e in selection["exclusions"])),
    )


def _issues(data):
    issues = []
    if data.get("previously_exposed_test"):
        issues.append(
            "Intended final-test identities have prior development/history exposure; collect new untouched evidence"
        )
    for name, group in data["development_partitions"].items():
        positive = sum(r["label"] for r in group)
        if (
            len(group) < ev.CONFIG["minimum_partition_rows"]
            or positive < ev.CONFIG["minimum_partition_positives"]
            or len(group) - positive < ev.CONFIG["minimum_partition_negatives"]
        ):
            issues.append(
                f"{name}: requires at least {ev.CONFIG['minimum_partition_rows']} mature rows and at least two examples of each outcome class"
            )
    if len(data["test_input_rows"]) < ev.CONFIG["minimum_partition_rows"]:
        issues.append("Insufficient unopened final-test prediction inputs; labels were not inspected")
    if not data["rows"] or len({r.get("outcome_source") for r in data["rows"]}) != 1:
        issues.append("Development data needs one comparable views source")
    return issues


def _declarations(
    *,
    synthetic=False,
    task="breakout_48h_v1",
    representative_sampling=False,
    comparison_population="",
    cohort=None,
    max_rows=5000,
    sampling_declaration="",
):
    return dict(
        synthetic=synthetic,
        task=task,
        representative_sampling=representative_sampling,
        comparison_population=comparison_population,
        cohort=cohort or {},
        max_rows=max_rows,
        sampling_declaration=sampling_declaration,
    )


def _prepare(store, declarations):
    errors = _configuration_errors(**declarations)
    data = prepare_development(
        store,
        task=declarations["task"],
        synthetic=declarations["synthetic"],
        max_rows=declarations["max_rows"],
        cohort=declarations["cohort"],
    )
    if not declarations["synthetic"]:
        for row in data["rows"] + data["test_input_rows"]:
            source = str(row.get("sampling_provenance") or "unknown").strip().lower()
            if source in ("manual", "unknown") or ev.re.search(r"winners?[_ -]?only|balanced", source):
                errors.append(
                    "Every intended cohort member requires documented representative collection provenance"
                )
                break
        policy = ev.CONFIG["promotion_policy"]
        if len(data["rows"]) + len(data["test_input_rows"]) < policy["minimum_eligible_rows"]:
            errors.append(
                f"Known cohort support is below the predeclared {policy['minimum_eligible_rows']} eligible-row requirement"
            )
        if len(data["test_input_rows"]) < policy["minimum_test_rows"]:
            errors.append(
                f"Known final-test prediction support is below the predeclared {policy['minimum_test_rows']} row requirement"
            )
        authors = {row["author"] for row in data["test_input_rows"] if row.get("author_known")}
        if len(authors) < policy["minimum_test_author_clusters"]:
            errors.append(
                "Known final-test author support is below the predeclared independent-author requirement"
            )
    return data, errors, _issues(data)


def preflight(store: Store, **kwargs):
    declarations = _declarations(**kwargs)
    data, errors, issues = _prepare(store, declarations)
    return {
        "status": "ready" if not errors and not issues else "not_ready",
        "ready_to_freeze": not errors and not issues,
        "workflow_version": WORKFLOW_VERSION,
        "configuration_errors": errors,
        "not_ready_reasons": errors + issues,
        "declarations": declarations,
        "configuration": declarations,
        "test_exposed": False,
        "split_manifest": data["split_manifest"],
        "development_summary": {
            "eligible_rows": len(data["rows"]),
            "positives": sum(r["label"] for r in data["rows"]),
            "candidate_count": data["development_candidate_count"],
            "exclusion_counts": data["exclusion_counts"],
            "failure_coverage": data["failure_coverage"],
        },
        "final_test_opened": False,
        "holdout_consumed": False,
        "limitations": [
            "Final-test observations were not read. Readiness is a development-only estimate; no predictor is promoted."
        ],
    }


def _new_report(data, declarations):
    synthetic = declarations["synthetic"]
    partitions = dict(data["development_partitions"], test=data["test_input_rows"])
    manifest = deepcopy(data["split_manifest"])
    manifest["intended_partitions"] = manifest["partitions"]
    manifest["partitions"] = {name: [r["key"] for r in partitions[name]] for name in ev.SPLITS}
    source = declarations["cohort"].get("outcome_source") or (
        data["rows"][0]["outcome_source"] if data["rows"] else None
    )
    audit = ev._cohort_audit(partitions)
    report = {
        "experiment_id": uid(),
        "created_at": now().isoformat(),
        "workflow_version": WORKFLOW_VERSION,
        "status": "not_ready",
        "synthetic": synthetic,
        "fixture_used": data["fixture_used"],
        "predictive_validation": "not_established",
        "task": declarations["task"],
        "label_definition": ev.CONFIG["label_definitions"][declarations["task"]],
        "policy_version": ev.CONFIG["version"],
        "declarations": declarations,
        "configuration": declarations,
        "test_exposed": False,
        "cohort_filter": declarations["cohort"],
        "dataset_hash": digest(
            {"development": data["rows"], "test_inputs": data["test_entries"] or data["test_input_rows"]}
        ),
        "eligibility": {
            k: data[k]
            for k in (
                "exclusions",
                "labels",
                "total_candidates",
                "coverage",
                "exclusion_counts",
                "configuration_signatures",
                "judgment_attempts",
                "judgment_selection_policy",
                "failure_coverage",
            )
        },
        "feature_schema": {
            "semantic": ev.SEMANTIC_NAMES,
            "metadata": ev.METADATA_NAMES,
            "version": "features_v1",
        },
        "promotion_policy": ev.CONFIG["promotion_policy"],
        "promotion_policy_hash": digest(ev.CONFIG["promotion_policy"]),
        "representative_sampling_attested": declarations["representative_sampling"],
        "comparison_population": declarations["comparison_population"],
        "sampling_declaration": declarations["sampling_declaration"],
        "forecast_available": False,
        "tier_boundaries": ev.CONFIG["tier_probability_boundaries"],
        "tier_semantics": ev.CONFIG["tier_semantics"],
        "models": {},
        "metrics": {},
        "audit": [],
        "split_manifest": manifest,
        "cohort_audit": audit,
        "cohort_audit_hash": digest(audit),
        "source_policy": {
            "metric": "views",
            "source": source,
            "mapping": None,
            "allow_missing_baseline": declarations["task"] == "absolute_48h_v1"
            and all(
                any(r["features"].get("log_baseline_views") is None for r in group)
                for group in data["development_partitions"].values()
            ),
        },
        "development_rows": data["rows"],
        "development_partitions": data["development_partitions"],
        "protected_test_keys": data["protected_test_keys"],
        "protected_test_identities": data["protected_test_identities"],
        "test_entries": data["test_entries"],
        "excluded_outcome_candidate_ids": data["excluded_outcome_candidate_ids"],
        "collection_counts": data["collection_counts"],
        "test_input_rows": data["test_input_rows"],
        "candidate_records": data["candidate_records"],
        "fixture_test_rows": [],
        "final_test_opened": False,
        "limitations": [
            "Development-only fitting does not establish final-test predictive accuracy or calibration.",
            "No causal rewrite effect or production X ranking claim is established.",
            "The final test can be opened once; failed gates require new untouched evidence.",
        ],
    }
    report["eligibility"].update(
        eligible_rows=len(data["rows"]), positives=sum(r["label"] for r in data["rows"])
    )
    return report


def development_evaluate(store: Store, **kwargs):
    declarations = _declarations(**kwargs)
    # Development exploration can proceed without deployment declarations. They
    # are mandatory for freezing/opening, and never retrospectively amended.
    _configuration_errors(**declarations)
    data = prepare_development(
        store,
        task=declarations["task"],
        synthetic=declarations["synthetic"],
        max_rows=declarations["max_rows"],
        cohort=declarations["cohort"],
    )
    report = _new_report(data, declarations)
    issues = _issues(data)
    if not issues:
        prediction = ev._fit_development(report, data["development_partitions"])
        _development_errors(report, prediction)
        report["status"] = "development_only"
    else:
        report["not_ready_reasons"] = issues
    report["promotion"] = {"eligible": False, "reasons": ["final_test_not_opened"], "approved": False}
    return ev._save(store, report)


def _development_errors(report, predictions):
    report["development_errors"] = [
        {
            "key": row["key"],
            "label": row["label"],
            "probability": float(p),
            "absolute_error": float(abs(row["label"] - p)),
            "partition": "selection",
        }
        for row, p in zip(report["development_partitions"]["selection"], predictions)
    ]


def freeze_evaluation(store: Store, **kwargs):
    declarations = _declarations(**kwargs)
    data, errors, issues = _prepare(store, declarations)
    report = _new_report(data, declarations)
    if errors or issues:
        report["configuration_errors"] = errors
        report["not_ready_reasons"] = errors + issues
        report["promotion"] = {
            "eligible": False,
            "reasons": ["preflight_requirements_not_met"],
            "approved": False,
        }
        return ev._save(store, report)
    prediction = ev._fit_development(report, data["development_partitions"])
    _development_errors(report, prediction)
    report["status"] = "frozen"
    report["holdout_id"] = digest({"task": report["task"], "keys": report["protected_test_keys"]})
    report["frozen_candidate_hash"] = digest(ev._frozen_payload(report))
    report["audit"].append(
        {
            "event": "candidate_and_declarations_frozen_without_test_outcomes",
            "at": now().isoformat(),
            "hash": report["frozen_candidate_hash"],
        }
    )
    report["promotion"] = {"eligible": False, "reasons": ["final_test_not_opened"], "approved": False}
    with store.transaction() as db:
        if not report["synthetic"] and _exposure_conflicts(db, report["protected_test_identities"]):
            report["status"] = "not_ready"
            report["not_ready_reasons"] = [
                "Intended final-test identities were exposed to development before freeze; use new untouched evidence"
            ]
            report["promotion"] = {
                "eligible": False,
                "reasons": ["development_exposure_overlap"],
                "approved": False,
            }
        else:
            db.execute(
                "INSERT INTO records(kind,id,body,created_at) VALUES('evaluation_freeze',?,?,?)",
                (report["experiment_id"], canonical(report), now().isoformat()),
            )
    return ev._save(store, report)


def _outcomes_for_open(store, report):
    identities, keys = _protected_identities(store)
    own_ids = {identity["candidate_id"] for identity in report["protected_test_identities"]}
    excluded = set(report["excluded_outcome_candidate_ids"])
    excluded.update(key.rsplit(":", 1)[0] for key in keys)
    for candidate in store.list("candidate"):
        if ev._overlaps_holdout([ev._holdout_identity(dict(candidate, key=ev._key(candidate)))], identities):
            excluded.add(candidate["candidate_id"])
    return _safe_outcomes(store, excluded - own_ids, authorized_test_ids=own_ids)


def open_holdout(store: Store, experiment_id: str, *, frozen_candidate_hash: str):
    frozen = store.get("evaluation_freeze", experiment_id)
    report = store.get("experiment", experiment_id)
    if not frozen or not report:
        raise ValueError("An immutable frozen candidate is required before opening final test")
    if (
        frozen_candidate_hash != frozen.get("frozen_candidate_hash")
        or digest(ev._frozen_payload(frozen)) != frozen_candidate_hash
        or digest(ev._frozen_payload(report)) != frozen_candidate_hash
    ):
        raise ValueError("Frozen candidate hash or immutable declarations changed")
    if _configuration_errors(**frozen["declarations"]):
        raise ValueError("Frozen configuration does not satisfy pre-test requirements")
    report = deepcopy(frozen)
    if not report["synthetic"]:
        with store.transaction() as db:
            if _exposure_conflicts(db, report["protected_test_identities"]):
                raise ValueError("Frozen final-test identities overlap prior development exposure")
            prior = [
                json.loads(r[0])
                for r in db.execute("SELECT body FROM records WHERE kind='holdout'").fetchall()
            ]
            old_keys = {key for r in prior for key in r.get("test_keys", [])}
            old_ids = {key.rsplit(":", 1)[0] for key in old_keys}
            identities = [identity for r in prior for identity in r.get("test_identities", [])]
            if (
                any(r["holdout_id"] == report["holdout_id"] for r in prior)
                or any(
                    i["key"] in old_keys or i["candidate_id"] in old_ids
                    for i in report["protected_test_identities"]
                )
                or ev._overlaps_holdout(report["protected_test_identities"], identities)
            ):
                return {
                    "experiment_id": experiment_id,
                    "status": "holdout_already_consumed",
                    "metrics": {},
                    "final_test_opened": False,
                    "not_ready_reasons": [
                        "Final-test identities were already opened; use new untouched or prospective evidence"
                    ],
                    "forecast_available": False,
                }
            record = {
                "holdout_id": report["holdout_id"],
                "experiment_id": experiment_id,
                "test_keys": report["protected_test_keys"],
                "test_identities": report["protected_test_identities"],
                "candidate_hash": frozen_candidate_hash,
                "consumed_at": now().isoformat(),
                "policy_hash": report["promotion_policy_hash"],
            }
            db.execute(
                "INSERT INTO records(kind,id,body,created_at) VALUES('holdout',?,?,?)",
                (report["holdout_id"], canonical(record), now().isoformat()),
            )
    elif store.get("experiment", experiment_id).get("final_test_opened"):
        return {
            "experiment_id": experiment_id,
            "status": "holdout_already_consumed",
            "metrics": {},
            "final_test_opened": False,
        }
    report["audit"].append(
        {"event": "holdout_opened", "at": now().isoformat(), "synthetic": report["synthetic"]}
    )
    report["final_test_opened"] = True
    report["test_exposed"] = True
    report["status"] = "opening_holdout"
    ev._save(store, report)  # A crash cannot silently put consumed data back into development.
    if report["fixture_used"]:
        test_keys = {row["key"] for row in report["test_input_rows"]}
        rows = [
            row
            for row in ev._synthetic_rows(min(400, report["declarations"]["max_rows"]))
            if row["key"] in test_keys
        ]
        labels, exclusions = [], []
    else:
        rows, labels, exclusions = ev._label_selected(
            report["test_entries"],
            report["candidate_records"],
            _outcomes_for_open(store, report),
            task=report["task"],
            outcome_source=report["source_policy"]["source"],
        )
    cluster_by_key = {r["key"]: r["cluster"] for r in report["test_input_rows"]}
    rows = [dict(row, cluster=cluster_by_key[row["key"]]) for row in rows]
    report["final_test_exclusions"] = exclusions
    report["final_test_labels"] = labels
    report["eligibility"]["eligible_rows"] += len(rows)
    report["eligibility"]["positives"] += sum(r["label"] for r in rows)
    report["split_manifest"]["counts"]["test"] = {
        "rows": len(rows),
        "positives": sum(r["label"] for r in rows),
    }
    report["split_manifest"]["eligible_partitions"]["test"] = [r["key"] for r in rows]
    positives = sum(r["label"] for r in rows)
    if (
        len(rows) < ev.CONFIG["minimum_partition_rows"]
        or positives < ev.CONFIG["minimum_partition_positives"]
        or len(rows) - positives < ev.CONFIG["minimum_partition_negatives"]
    ):
        report["status"] = "not_ready_after_open"
        report["not_ready_reasons"] = [
            "Opened test lacks required mature examples of both classes. It remains consumed; collect new untouched evidence."
        ]
        report["promotion"] = {
            "eligible": False,
            "reasons": ["insufficient_opened_test_support"],
            "approved": False,
        }
        return ev._save(store, report)
    partitions = dict(report["development_partitions"], test=rows)
    selection_predictions = [e["probability"] for e in report["development_errors"]]
    return ev._finish_evaluation(store, report, partitions, selection_predictions)
