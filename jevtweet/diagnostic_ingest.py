"""Lossless, private ingestion of date-only text exports with unwindowed snapshots.

The adapter never creates historical outcome observations or infers authorship. Local
IDs depend only on original content, post type and media presence, with an occurrence
number for identical duplicates. Rank, source order, dates and metrics cannot select
an ID. Identical duplicate rows are indistinguishable; their set of IDs is stable.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import statistics
from collections import Counter
from datetime import date
from pathlib import Path
from uuid import UUID, uuid5

from .contracts import Candidate, Media, PredictionContext, canonical, now
from .corpus import candidate_key
from .diagnostic_contracts import DiagnosticRestriction, DiagnosticSourceRecord, MetricSnapshot
from .storage import Store

ADAPTER_VERSION = "snapshot_csv_v1"
_NAMESPACE = UUID("02ea1c0e-fbdb-5d73-a58a-47b5db3d523f")
_COLUMNS = (
    "Rank",
    "Date",
    "Post Type",
    "Theme",
    "Hook (first line)",
    "Full Text",
    "Likes",
    "Reposts",
    "Quotes",
    "Replies",
    "Bookmarks",
    "Views",
    "Has Media",
    "Engagement Score",
    "Word Count",
    "Char Count",
)
_NUMERIC_COLUMNS = (
    "Rank",
    "Likes",
    "Reposts",
    "Quotes",
    "Replies",
    "Bookmarks",
    "Views",
    "Engagement Score",
    "Word Count",
    "Char Count",
)
_COUNT_PATTERN = re.compile(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)\Z")
_ARTIFACT_NAMES = {
    "source.csv",
    "normalized_source.jsonl",
    "metric_snapshots.jsonl",
    "candidates.jsonl",
    "contexts.jsonl",
    "data_quality.json",
}


def _checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_private(path: Path, content: bytes) -> None:
    with path.open("wb") as stream:
        os.chmod(path, 0o600)
        stream.write(content)


def _write_json(path: Path, value: dict) -> None:
    _write_private(path, (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode())


def _write_jsonl(path: Path, records: list[dict]) -> None:
    _write_private(path, "".join(canonical(record) + "\n" for record in records).encode())


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _integer(raw: str, column: str, index: int) -> int | None:
    value = raw.strip()
    if not value:
        return None
    if not _COUNT_PATTERN.fullmatch(value):
        raise ValueError(f"Record {index}: {column} must be a nonnegative integer or empty")
    return int(value.replace(",", ""))


def _date(raw: str, index: int) -> str | None:
    if not raw.strip():
        return None
    try:
        parsed = date.fromisoformat(raw.strip())
    except ValueError as exc:
        raise ValueError(f"Record {index}: Date must be an ISO calendar date or empty") from exc
    if raw.strip() != parsed.isoformat():
        raise ValueError(f"Record {index}: Date must be an ISO calendar date or empty")
    return parsed.isoformat()


def _parse_source(content: bytes) -> tuple[list[str], list[dict[str, str]]]:
    # newline='' preserves quoted CR/LF sequences in original text rather than
    # applying universal newline translation. utf-8-sig supports an optional BOM.
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
    columns = reader.fieldnames
    if not columns or len(columns) != len(set(columns)):
        raise ValueError("CSV must contain distinct column names")
    missing = set(_COLUMNS) - set(columns)
    if missing:
        raise ValueError("CSV is missing required columns: " + ", ".join(sorted(missing)))
    rows = []
    for index, row in enumerate(reader, 1):
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"Record {index}: column count differs from the header")
        rows.append(row)
    return columns, rows


def _audit(columns, rows, numeric, dates, sidecars) -> dict:
    views = [row["Views"] for row in numeric if row["Views"] is not None]
    scores = [row["Engagement Score"] for row in numeric]
    texts = [row["Full Text"] for row in rows]
    known_dates = [value for value in dates if value is not None]
    arithmetic_complete = 0
    arithmetic_matches = 0
    word_available = character_available = word_matches = character_matches = 0
    for row, counts in zip(rows, numeric, strict=True):
        formula_columns = ["Likes", "Reposts", "Quotes", "Replies", "Bookmarks", "Engagement Score"]
        if all(counts[column] is not None for column in formula_columns):
            arithmetic_complete += 1
            derived = (
                counts["Likes"]
                + 2 * counts["Reposts"]
                + 2 * counts["Quotes"]
                + counts["Replies"]
                + 2 * counts["Bookmarks"]
            )
            arithmetic_matches += derived == counts["Engagement Score"]
        if counts["Word Count"] is not None:
            word_available += 1
            word_matches += len(row["Full Text"].split()) == counts["Word Count"]
        if counts["Char Count"] is not None:
            character_available += 1
            character_matches += len(row["Full Text"]) == counts["Char Count"]

    def descending(values):
        if any(value is None for value in values) or not values:
            return None
        return all(left >= right for left, right in zip(values, values[1:]))

    return {
        "schema_version": "1",
        "adapter_version": ADAPTER_VERSION,
        "status": "audited",
        "record_count": len(rows),
        "column_count": len(columns),
        "columns": columns,
        "extra_columns_preserved": sorted(set(columns) - set(_COLUMNS)),
        "source_dates": {
            "min": min(known_dates) if known_dates else None,
            "max": max(known_dates) if known_dates else None,
            "missing_count": len(rows) - len(known_dates),
            "precision": "calendar_date_only",
        },
        "post_types": dict(Counter(row["Post Type"] for row in rows)),
        "media_flags": dict(Counter(row["Has Media"] for row in rows)),
        "post_type_media_crosstab": dict(
            Counter(f"{sidecar.post_type}/{sidecar.media_presence}" for sidecar in sidecars)
        ),
        "themes_as_supplied": dict(Counter(row["Theme"] for row in rows)),
        "empty_cell_count": sum(value == "" for row in rows for value in row.values()),
        "empty_cells_by_column": {column: sum(row[column] == "" for row in rows) for column in columns},
        "exact_duplicate_text_count": len(texts) - len(set(texts)),
        "multiline_text_count": sum("\n" in text or "\r" in text for text in texts),
        "views_snapshot": {
            "available_count": len(views),
            "missing_count": len(rows) - len(views),
            "min": min(views) if views else None,
            "median": statistics.median(views) if views else None,
            "max": max(views) if views else None,
            "at_least_10000_count": sum(value >= 10000 for value in views),
        },
        "zero_supplied_engagement_score_count": sum(value == 0 for value in scores),
        "supplied_engagement_score_descending": descending(scores),
        "views_descending": descending([row["Views"] for row in numeric]),
        "engagement_arithmetic": {
            "formula": "Likes + 2*Reposts + 2*Quotes + Replies + 2*Bookmarks",
            "available_count": arithmetic_complete,
            "matching_count": arithmetic_matches,
            "status": "inferred arithmetic check; not documented source methodology or editorial rubric",
        },
        "word_count_check": {
            "method": "Python whitespace splitting",
            "available_count": word_available,
            "matching_count": word_matches,
        },
        "character_count_check": {
            "method": "Python Unicode code-point length",
            "available_count": character_available,
            "matching_count": character_matches,
        },
        "initial_cohort_eligible_count": sum(sidecar.initial_cohort_eligible for sidecar in sidecars),
        "limitations": [
            "Counts are unwindowed snapshots, not 48-hour observations or breakout labels.",
            "Calendar dates do not establish exact publication times or timezone.",
            "Sampling rule, completeness, source authenticity and account attribution are unverified.",
            "Themes are supplied metadata, not independently validated findings.",
            "No author history, media description, parent conversation or quoted content is inferred.",
            "All source records remain development-exposed diagnostic material permanently.",
        ],
    }


def _verify_artifacts(destination: Path, manifest: dict, source_hash: str) -> None:
    if manifest.get("source_sha256") != source_hash:
        raise ValueError("Destination already contains a different source checksum")
    artifacts = manifest.get("artifacts", {})
    if set(artifacts) != _ARTIFACT_NAMES:
        raise ValueError("Diagnostic artifact integrity failed: missing or unknown artifact declarations")
    for filename, expected in artifacts.items():
        path = destination / filename
        if not path.is_file():
            raise ValueError(f"Diagnostic artifact integrity failed: missing {filename}")
        content = path.read_bytes()
        if expected != {"sha256": _checksum(content), "size_bytes": len(content)}:
            raise ValueError(f"Diagnostic artifact checksum mismatch: {filename}")


def _hydrate_store(store: Store, destination: Path, manifest: dict) -> None:
    from .research_restrictions import register_diagnostic_only

    candidates = [Candidate.model_validate(row) for row in _read_jsonl(destination / "candidates.jsonl")]
    contexts = _read_jsonl(destination / "contexts.jsonl")
    snapshots = [
        MetricSnapshot.model_validate(row) for row in _read_jsonl(destination / "metric_snapshots.jsonl")
    ]
    sidecars = [
        DiagnosticSourceRecord.model_validate(row)
        for row in _read_jsonl(destination / "normalized_source.jsonl")
    ]
    # Record exposure first: an interrupted ingest must not create unrestricted
    # candidates that could subsequently enter a held-out experiment.
    register_diagnostic_only(store, candidates, manifest["source_id"])
    pending = [
        *(
            ("candidate", candidate_key(candidate), candidate.model_dump(mode="json"))
            for candidate in candidates
        ),
        *(
            (
                "context",
                row["candidate_key"],
                PredictionContext.model_validate(row["context"]).model_dump(mode="json"),
            )
            for row in contexts
        ),
        *(("diagnostic_snapshot", row.snapshot_id, row.model_dump(mode="json")) for row in snapshots),
        *(
            (
                "diagnostic_source_record",
                f"{row.source_id}:{row.source_record_index}",
                row.model_dump(mode="json"),
            )
            for row in sidecars
        ),
    ]
    # Validate all identities in one transaction; malformed/conflicting imports
    # cannot leave a partially inserted corpus in this database.
    with store.transaction() as db:
        for kind, record_id, record in pending:
            body = canonical(record)
            old = db.execute("SELECT body FROM records WHERE kind=? AND id=?", (kind, record_id)).fetchone()
            if old and old[0] != body:
                raise ValueError(f"Conflicting immutable {kind} identity: {record_id}")
        for kind, record_id, record in pending:
            db.execute(
                "INSERT OR IGNORE INTO records(kind,id,body,created_at) VALUES(?,?,?,?)",
                (kind, record_id, canonical(record), manifest["imported_at"]),
            )


def ingest_snapshot_csv(
    source: Path,
    destination: Path,
    *,
    store: Store | None = None,
    attribution_assumption: str | None = None,
) -> dict:
    """Archive a private CSV and create honest, development-only sidecars.

    Use a dedicated ignored/private destination. An existing manifest is immutable:
    reimport verifies every artifact and may hydrate a new private database, but
    never changes an earlier ingestion timestamp or source attribution assumption.
    """
    source, destination = Path(source), Path(destination)
    content = source.read_bytes()
    source_hash = _checksum(content)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest_path = destination / "source_manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        _verify_artifacts(destination, manifest, source_hash)
        if attribution_assumption is not None and attribution_assumption != manifest.get(
            "attribution_assumption"
        ):
            raise ValueError("Existing source attribution assumption is immutable")
        if store is not None:
            _hydrate_store(store, destination, manifest)
        return manifest
    archive_path = destination / "source.csv"
    if archive_path.exists() and archive_path.read_bytes() != content:
        raise ValueError("Destination archive contains a different source checksum")
    if not archive_path.exists():
        _write_private(archive_path, content)

    stamp = now()
    imported_at = stamp.isoformat().replace("+00:00", "Z")
    source_id = str(uuid5(_NAMESPACE, "source:" + source_hash))
    candidates, contexts, snapshots, sidecars, numeric, dates = [], [], [], [], [], []
    occurrences: Counter[str] = Counter()
    try:
        columns, rows = _parse_source(content)
        for index, raw in enumerate(rows, 1):
            counts = {column: _integer(raw[column], column, index) for column in _NUMERIC_COLUMNS}
            calendar_date = _date(raw["Date"], index)
            post_type = raw["Post Type"].strip().lower()
            if post_type not in {"original", "reply", "quote", "thread"}:
                raise ValueError(
                    f"Record {index}: unsupported Post Type; preserve source and supply an explicit mapping"
                )
            media_presence = {"yes": "yes", "no": "no"}.get(raw["Has Media"].strip().lower(), "unknown")
            identity = canonical(
                {"text": raw["Full Text"], "post_type": post_type, "media_presence": media_presence}
            )
            occurrence = occurrences[identity]
            occurrences[identity] += 1
            candidate_id = str(uuid5(_NAMESPACE, canonical([identity, occurrence])))
            candidate = Candidate(
                candidate_id=candidate_id,
                text=raw["Full Text"],
                language="und",
                post_type=post_type,
                content_available_at=stamp,
                distribution="unknown",
                synthetic=False,
                provenance="diagnostic_snapshot_csv_v1",
                media=Media(
                    kind="none" if media_presence == "no" else "other", essential=media_presence != "no"
                ),
            )
            context = PredictionContext(prediction_cutoff=stamp, evaluation_split="development")
            limitations = []
            if post_type == "reply":
                limitations.append("missing_parent_context")
            elif post_type == "quote":
                limitations.append("missing_quoted_context")
            elif post_type == "thread":
                limitations.append("missing_thread_context")
            if media_presence == "yes":
                limitations.append("media_present_without_description")
            elif media_presence == "unknown":
                limitations.append("unknown_media_presence")
            sidecar = DiagnosticSourceRecord(
                source_id=source_id,
                candidate_id=candidate_id,
                source_record_index=index,
                raw=raw,
                source_calendar_date=calendar_date,
                source_date_precision="calendar_date_only" if calendar_date else "unknown",
                imported_at=stamp,
                post_type=post_type,
                media_presence=media_presence,
                initial_cohort_eligible=post_type == "original" and media_presence == "no",
                limitations=limitations,
            )
            snapshot = MetricSnapshot(
                snapshot_id=str(uuid5(_NAMESPACE, f"snapshot:{source_id}:{candidate_id}")),
                source_id=source_id,
                candidate_id=candidate_id,
                imported_at=stamp,
                views=counts["Views"],
                likes=counts["Likes"],
                reposts=counts["Reposts"],
                quotes=counts["Quotes"],
                replies=counts["Replies"],
                bookmarks=counts["Bookmarks"],
                supplied_engagement_score=counts["Engagement Score"],
            )
            candidates.append(candidate)
            contexts.append(
                {
                    "candidate_id": candidate_id,
                    "candidate_key": candidate_key(candidate),
                    "context": context.model_dump(mode="json"),
                }
            )
            snapshots.append(snapshot)
            sidecars.append(sidecar)
            numeric.append(counts)
            dates.append(calendar_date)
    except (ValueError, UnicodeError, csv.Error) as exc:
        _write_json(
            destination / "data_quality.json",
            {
                "schema_version": "1",
                "adapter_version": ADAPTER_VERSION,
                "status": "rejected",
                "source_sha256": source_hash,
                "source_size_bytes": len(content),
                "imported_at": imported_at,
                "error": str(exc),
                "archive_preserved": True,
            },
        )
        raise ValueError(str(exc)) from exc

    audit = _audit(columns, rows, numeric, dates, sidecars)
    audit.update(source_sha256=source_hash, source_size_bytes=len(content), imported_at=imported_at)
    _write_jsonl(destination / "candidates.jsonl", [row.model_dump(mode="json") for row in candidates])
    _write_jsonl(destination / "contexts.jsonl", contexts)
    _write_jsonl(destination / "metric_snapshots.jsonl", [row.model_dump(mode="json") for row in snapshots])
    _write_jsonl(destination / "normalized_source.jsonl", [row.model_dump(mode="json") for row in sidecars])
    _write_json(destination / "data_quality.json", audit)
    manifest = {
        "schema_version": "1",
        "adapter_version": ADAPTER_VERSION,
        "source_id": source_id,
        "source_sha256": source_hash,
        "source_filename": source.name,
        "source_size_bytes": len(content),
        "imported_at": imported_at,
        "attribution_assumption": attribution_assumption,
        "restrictions": DiagnosticRestriction().model_dump(mode="json"),
        "candidate_ids": [candidate.candidate_id for candidate in candidates],
        "candidate_keys": [candidate_key(candidate) for candidate in candidates],
        "eligible_candidate_ids": [row.candidate_id for row in sidecars if row.initial_cohort_eligible],
        "eligible_candidate_keys": [
            f"{row.candidate_id}:1" for row in sidecars if row.initial_cohort_eligible
        ],
        "excluded_candidates": [
            {
                "candidate_id": row.candidate_id,
                "candidate_key": f"{row.candidate_id}:1",
                "reason_codes": row.limitations,
            }
            for row in sidecars
            if not row.initial_cohort_eligible
        ],
        "record_count": len(rows),
        "column_count": len(columns),
        "artifacts": {
            filename: {
                "sha256": _checksum((destination / filename).read_bytes()),
                "size_bytes": (destination / filename).stat().st_size,
            }
            for filename in sorted(_ARTIFACT_NAMES)
        },
    }
    _write_json(manifest_path, manifest)
    if store is not None:
        _hydrate_store(store, destination, manifest)
    return manifest
