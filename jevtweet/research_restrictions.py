"""Private, permanent diagnostic-only membership, independent of editable metadata.

An explicitly registered source can be judged editorially and inspected in a
diagnostic report. It cannot train, calibrate, test, promote or receive a research
forecast. The registry is shared through the configured account directory, with
a local copy for retained datasets. It stores private token identities, never
outcomes, and reuses the established holdout alias/near-duplicate policy.
"""

from __future__ import annotations

import json
from pathlib import Path

from .contracts import Candidate, digest
from .settings import Settings
from .storage import Store

REASON = "permanent_diagnostic_only_corpus"
POLICY_VERSION = "diagnostic_restriction_v1"


def _record(candidate: Candidate | dict) -> dict:
    return candidate.model_dump(mode="json") if isinstance(candidate, Candidate) else candidate


def _identity(candidate: Candidate | dict) -> dict:
    # Local import keeps the evaluator authoritative for the existing identity
    # semantics, without an evaluator/registry module-initialization cycle.
    from . import evaluation as ev

    candidate = _record(candidate)
    key = candidate.get("key") or ev._key(candidate)
    if "tokens" in candidate and "content_hash" in candidate:
        return {k: candidate.get(k) for k in ("key", "candidate_id", "thread_id", "content_hash", "tokens")}
    if "text" in candidate:
        return ev._holdout_identity(dict(candidate, key=key))
    return {
        "key": key,
        "candidate_id": candidate["candidate_id"],
        "thread_id": candidate.get("thread_id"),
        "content_hash": digest({"identity_without_text": key}),
        "tokens": [],
    }


def _registry(store: Store, *, create: bool = False) -> Store | None:
    directory = Path(getattr(store, "restriction_registry_dir", Settings().account_dir))
    if directory.resolve() == store.data_dir.resolve():
        return store
    if not create and not (directory / "jevtweet.sqlite3").exists():
        return None
    return Store(directory)


def register_diagnostic_only(
    store: Store,
    candidates: list[Candidate | dict],
    source_id: str,
    *,
    registry: Store | None = None,
) -> list[dict]:
    """Register irreversibly; repeated identical registration is idempotent.

    No release/update API exists. Changing IDs, versions, source flags, invented
    observation windows or synthetic flags cannot remove identity membership.
    Filesystem administrators must preserve the configured shared registry.
    """
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("A nonempty private diagnostic source identity is required")
    registry = registry or _registry(store, create=True)
    records = []
    for candidate in candidates:
        identity = _identity(candidate)
        restriction_id = digest({"source_id": source_id, "identity": identity})
        record = {
            "restriction_id": restriction_id,
            "policy_version": POLICY_VERSION,
            "source_id": source_id,
            "reason": REASON,
            "identity": identity,
        }
        # Global registration happens first: interruption cannot leave a source
        # locally marked while silently available to another dataset.
        registry.put("diagnostic_restriction", restriction_id, record)
        store.put("diagnostic_restriction", restriction_id, record)
        records.append(record)
    return records


def restricted_candidates(store: Store, candidates: list[Candidate | dict]) -> dict[str, dict]:
    from . import evaluation as ev

    if not candidates:
        return {}
    records = {r["restriction_id"]: r for r in store.list("diagnostic_restriction")}
    registry = _registry(store)
    if registry is not None and registry.path.resolve() != store.path.resolve():
        records.update({r["restriction_id"]: r for r in registry.list("diagnostic_restriction")})
    found = {}
    for candidate in candidates:
        identity = _identity(candidate)
        for record in records.values():
            if ev._overlaps_holdout([identity], [record["identity"]]):
                found[identity["key"]] = {
                    "reason": REASON,
                    "source_id": record["source_id"],
                    "restriction_id": record["restriction_id"],
                }
                break
    return found


def research_outcomes(store: Store, candidates: list[dict]) -> tuple[list[dict], list[dict]]:
    """Filter identity membership before loading any restricted outcome body."""
    from . import evaluation as ev

    restrictions = restricted_candidates(store, candidates)
    excluded_ids = {c["candidate_id"] for c in candidates if ev._key(c) in restrictions}
    allowed = [c for c in candidates if c["candidate_id"] not in excluded_ids]
    clause = (
        " AND json_extract(body, '$.candidate_id') NOT IN (" + ",".join("?" for _ in excluded_ids) + ")"
        if excluded_ids
        else ""
    )
    with store.connect() as db:
        rows = db.execute(
            "SELECT body FROM records WHERE kind='outcome'" + clause, sorted(excluded_ids)
        ).fetchall()
    return allowed, [json.loads(row[0]) for row in rows]


def report_restrictions(store: Store, report: dict) -> dict[str, dict]:
    """Check used lineage, not unrelated candidates present in the same store."""
    from . import evaluation as ev

    candidates = list(report.get("development_rows", []))
    candidates.extend(report.get("test_input_rows", []))
    candidates.extend(report.get("protected_test_identities", []))
    candidates.extend(entry["candidate"] for entry in report.get("test_entries", []))
    for field in ("development_partitions", "cohort_audit"):
        for rows in report.get(field, {}).values():
            candidates.extend(rows)
    labels = list(report.get("eligibility", {}).get("labels", []))
    labels.extend(report.get("final_test_labels", []))
    for row in list(candidates):
        labels.append(row.get("label_details", {}))
        candidates.extend(row.get("references", []))
    legacy_observations = set()
    for label in labels:
        candidates.extend(label.get("baseline_identities", []))
        if not label.get("baseline_identities"):
            legacy_observations.update(label.get("baseline_observation_ids", []))
    # Older reports identify historical evidence by observation ID. Resolve
    # only its candidate identity; no views, labels or other outcome values are
    # read. New reports freeze identities directly and survive report copying.
    snapshots = {ev._key(c): c for c in report.get("candidate_records", [])}
    observations = sorted(legacy_observations)
    with store.connect() as db:
        for start in range(0, len(observations), 500):
            group = observations[start : start + 500]
            for candidate_id, version in db.execute(
                "SELECT json_extract(body, '$.candidate_id'), "
                "COALESCE(json_extract(body, '$.candidate_version'),1) "
                "FROM records WHERE kind='outcome' AND id IN (" + ",".join("?" for _ in group) + ")",
                group,
            ):
                key = f"{candidate_id}:{version}"
                candidates.append(
                    snapshots.get(key)
                    or store.get("candidate", key)
                    or {"candidate_id": candidate_id, "candidate_version": version}
                )
    return restricted_candidates(store, candidates)
