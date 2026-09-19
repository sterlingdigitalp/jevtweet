"""Private source-preserving archive normalization, without provider or outcome access.

Only explicitly selected primary/gapfill content creates targets. All source
versions remain inspectable. Source claims and selected raw records are not
provider-ready evidence; downstream strict allowlists and context checks apply.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import stat
import zipfile
from collections import defaultdict
from pathlib import Path, PurePosixPath

from .contracts import canonical, digest

MAX_ARCHIVE_BYTES = 100_000_000
MAX_EXPANDED_BYTES = 250_000_000
MAX_FILE_BYTES = 25_000_000
MAX_FILES = 1000
MAX_RECORDS = 50_000
ROLES = {
    "primary_content",
    "gapfill_content",
    "metrics",
    "gapfill_metrics",
    "orphan_metrics",
    "historical_content",
    "historical_metrics",
    "quote_context",
    "thread_context",
    "article_context",
    "ancillary",
    "summary",
    "primary_summary",
    "historical_summary",
}
_CONTENT_ROLES = {"primary_content", "gapfill_content", "historical_content"}
_ACTIVE_CONTENT = {"primary_content", "gapfill_content"}
_METRIC_ROLES = {"metrics", "gapfill_metrics", "orphan_metrics", "historical_metrics"}
_ACTIVE_METRICS = {"metrics", "gapfill_metrics"}
_COUNT_FIELDS = ("likes", "reposts", "quotes", "replies", "bookmarks", "views", "impressions")
_CORE_FIELDS = {
    "text",
    "post_type",
    "created_at_utc",
    "is_quote",
    "is_reply",
    "is_repost",
    "is_article",
    "is_thread_starter",
    "has_media",
    "media_type",
    "media_count",
    "media_count_status",
    "quoted_id",
    "parent_id",
    "article_url",
    "quoted_text",
    "author_id",
    "author_handle",
}
_PRIORITY = {
    "primary_content": 0,
    "gapfill_content": 1,
    "historical_content": 2,
    "metrics": 0,
    "gapfill_metrics": 1,
    "orphan_metrics": 2,
    "historical_metrics": 3,
}


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("Unsafe archive path")
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise ValueError("Unsafe absolute archive path")
    value = value.removesuffix("/")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("Unsafe archive path component")
    return str(PurePosixPath(value))


def _private_directory(destination: Path) -> Path:
    destination = destination.expanduser().resolve()
    root = Path(__file__).resolve().parent.parent
    if destination.is_relative_to(root) and not destination.is_relative_to(root / "private"):
        raise ValueError("Source archives inside this repository must be under private/")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(destination, 0o700)
    return destination


def _write_once(path: Path, content: bytes) -> None:
    if path.is_symlink():
        raise ValueError("Refusing a symlink artifact path")
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError(f"Immutable derived artifact differs: {path.name}")
        return
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(content)


def _json_bytes(value) -> bytes:
    return (canonical(value) + "\n").encode()


def _parse_payload(path: str, body: bytes) -> list[tuple[int, dict]]:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix not in {".json", ".jsonl", ".csv"}:
        return []
    text = body.decode("utf-8-sig")
    if suffix == ".jsonl":
        parsed = [
            (line, json.loads(value)) for line, value in enumerate(text.splitlines(), 1) if value.strip()
        ]
    elif suffix == ".json":
        value = json.loads(text)
        if isinstance(value, list):
            # Preserve each top-level source version, its actual line and the
            # array index added below, even when a minified array shares lines.
            parsed = []
            decoder = json.JSONDecoder()
            position = text.index("[") + 1
            for _ in value:
                while text[position].isspace() or text[position] == ",":
                    position += 1
                record, stop = decoder.raw_decode(text, position)
                parsed.append((text.count("\n", 0, position) + 1, record))
                position = stop
        else:
            parsed = [(1, value)]
    else:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError(f"Invalid CSV header: {path}")
        parsed = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Invalid CSV column count: {path}")
            parsed.append((reader.line_num, row))
    if len(parsed) > MAX_RECORDS:
        raise ValueError("Archive record limit exceeded")
    if suffix in {".jsonl", ".csv"} and any(not isinstance(row, dict) for _, row in parsed):
        raise ValueError(f"Source record must be an object: {path}")
    return parsed


def _read_verified(content: bytes, selection: dict):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_FILES or sum(info.file_size for info in infos) > MAX_EXPANDED_BYTES:
            raise ValueError("Archive expanded size or file count limit exceeded")
        names = set()
        payloads = {}
        for info in infos:
            name = _path(info.filename)
            if name in names:
                raise ValueError("Archive contains duplicate member paths")
            names.add(name)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError("Archive symlink members are forbidden")
            if info.file_size > MAX_FILE_BYTES:
                raise ValueError("Archive individual file size limit exceeded")
            if info.flag_bits & 1:
                raise ValueError("Encrypted source archive members are unsupported")
            if not info.is_dir():
                payloads[name] = archive.read(info)
    manifests = [path for path in payloads if PurePosixPath(path).name == "MANIFEST.json"]
    requested = selection.get("manifest_path")
    if requested is not None:
        requested = _path(requested)
        manifests = [path for path in manifests if path == requested]
    if len(manifests) != 1:
        raise ValueError("A unique supplied MANIFEST.json is required; select manifest_path explicitly")
    manifest_path = manifests[0]
    parent = PurePosixPath(manifest_path).parent
    prefix = "" if str(parent) == "." else str(parent) + "/"
    if any(not path.startswith(prefix) for path in payloads):
        raise ValueError("Archive payload lies outside the selected manifest directory")
    payloads = {path.removeprefix(prefix): body for path, body in payloads.items()}
    manifest = json.loads(payloads["MANIFEST.json"].decode("utf-8-sig"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise ValueError("Supplied manifest must declare a files list")
    entries = {}
    discrepancies = []
    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            raise ValueError("Malformed manifest file entry")
        path = _path(entry.get("path"))
        if path in entries or path not in payloads:
            raise ValueError("Manifest contains duplicate or missing payload paths")
        entries[path] = entry
        actual = {"bytes": len(payloads[path]), "sha256": _sha(payloads[path])}
        if entry.get("bytes") != actual["bytes"] or entry.get("sha256") != actual["sha256"]:
            if path != "MANIFEST.json":
                raise ValueError(f"Manifest payload checksum/size mismatch: {path}")
            discrepancies.append(
                {
                    "kind": "stale_self_manifest_entry",
                    "path": path,
                    "recorded": {key: entry.get(key) for key in actual},
                    "actual": actual,
                    "handling": "Original manifest preserved; every non-manifest entry remains strict.",
                }
            )
    if set(payloads) - set(entries) - {"MANIFEST.json"}:
        raise ValueError("Archive contains unlisted payloads absent from supplied manifest")
    roles = selection.get("files")
    if not isinstance(roles, dict) or not roles:
        raise ValueError("Selection must contain an explicit files role mapping")
    for path, role in roles.items():
        if _path(path) != path or path not in payloads or role not in ROLES:
            raise ValueError("Selection contains an unknown source path or role")
        if role in _ACTIVE_CONTENT | _ACTIVE_METRICS | {"orphan_metrics"} and not path.endswith(".jsonl"):
            raise ValueError("Content and metric authority must be selected from JSONL files")
    files, versions = [], []
    for path, body in sorted(payloads.items()):
        parsed = _parse_payload(path, body)
        row_count = len(parsed) if path.endswith((".jsonl", ".csv")) else None
        expected_rows = entries.get(path, {}).get("jsonl_rows")
        if expected_rows is not None and expected_rows != row_count:
            raise ValueError(f"Manifest JSONL row count mismatch: {path}")
        role = roles.get(path, "ancillary")
        files.append(
            {
                "path": path,
                "sha256": _sha(body),
                "size_bytes": len(body),
                "role": role,
                "row_count": row_count,
            }
        )
        for record_index, (line, record) in enumerate(parsed, 1):
            if path == "MANIFEST.json":
                continue
            version = {
                "path": path,
                "line": line,
                "record_index": record_index,
                "role": role,
                "record": record,
            }
            versions.append({"version_id": digest(version), **version})
    if len(versions) > MAX_RECORDS:
        raise ValueError("Archive total record limit exceeded")
    integrity = {
        "manifest_path": manifest_path,
        "verified_payload_count": len(payloads) - 1,
        "manifest_payload_count": len(entries) - int("MANIFEST.json" in entries),
        "discrepancies": discrepancies,
        "unlisted_payloads": [],
        "authenticity": "Archived bytes verified against supplied hashes; source claims remain unverified.",
    }
    return manifest, files, versions, integrity


def _post_id(record: dict) -> str:
    value = record.get("post_id")
    if (
        isinstance(value, bool)
        or not isinstance(value, (str, int))
        or not str(value)
        or str(value).strip() != str(value)
    ):
        raise ValueError("Content and metric records require an exact nonempty post_id")
    return str(value)


def _ordered(versions):
    return sorted(versions, key=lambda item: (_PRIORITY.get(item["role"], 9), item["path"], item["line"]))


def _field_resolution(versions, selected):
    active = [version for version in versions if version["role"] in _ACTIVE_CONTENT]
    fields = sorted({field for version in versions for field in version["record"]})
    resolution, conflicts = {}, []
    for field in fields:
        supplied = [version for version in active if field in version["record"]]
        distinct = {canonical(version["record"][field]) for version in supplied}
        conflict = len(distinct) > 1
        if conflict and field in _CORE_FIELDS:
            conflicts.append(field)
        value = selected["record"].get(field)
        historical = [
            version
            for version in versions
            if version["role"] == "historical_content" and field in version["record"]
        ]
        item = {
            "status": "unresolved_conflict"
            if conflict and field in _CORE_FIELDS
            else "source_declaration_variation"
            if conflict
            else "selected_source_value"
            if field in selected["record"]
            else "not_in_selected_source",
            "policy": "Primary over gapfill; deterministic path/line tie break; no field backfill or longest-text selection.",
            "selected_version_id": selected["version_id"],
            "selected_present": field in selected["record"],
            "selected_value": value,
            "historical_difference": any(
                canonical(version["record"][field]) != canonical(value) for version in historical
            ),
            "versions": [
                {
                    "version_id": version["version_id"],
                    "present": field in version["record"],
                    "value": version["record"].get(field),
                }
                for version in versions
            ],
        }
        if field in {
            "author_id",
            "author_handle",
            "author_name",
            "author_id_note",
            "provenance",
            "source_tool",
        }:
            item["verification"] = "source_claim_unverified"
        resolution[field] = item
    return resolution, conflicts


def _counts(version):
    values = {}
    for field in _COUNT_FIELDS:
        raw = version["record"].get(field)
        if raw is None or raw == "":
            value = None
        elif type(raw) is int and raw >= 0:
            value = raw
        elif isinstance(raw, str) and re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)", raw):
            value = int(raw.replace(",", ""))
        else:
            raise ValueError(f"Invalid nonnegative snapshot count {field} in {version['path']}")
        values[field] = value
    return values


def _metric_resolution(versions):
    active = _ordered([version for version in versions if version["role"] in _ACTIVE_METRICS])
    counts = {version["version_id"]: _counts(version) for version in versions}
    conflicts = [
        field
        for field in _COUNT_FIELDS
        if len({counts[version["version_id"]][field] for version in active}) > 1
    ]
    selected = active[0] if active and not conflicts else None
    status = (
        "unresolved_count_conflict"
        if conflicts
        else "missing_authoritative_metric"
        if not selected
        else "equivalent_counts_primary_precedence"
        if len(active) > 1
        else "selected_primary"
        if selected["role"] == "metrics"
        else "selected_gapfill"
    )
    return selected["record"] if selected else None, {
        "status": status,
        "conflicts": conflicts,
        "selected_version_id": selected["version_id"] if selected else None,
        "normalized_counts": counts[selected["version_id"]]
        if selected
        else {field: None for field in _COUNT_FIELDS},
        "counts_agree": not conflicts if active else None,
        "observed_at_claims": [
            {
                "version_id": version["version_id"],
                "value": version["record"].get("observed_at"),
                "metric_source": version["record"].get("metric_source"),
                "measurement_window": version["record"].get("measurement_window"),
            }
            for version in versions
        ],
        "claims_are_independent_observations": False,
        "policy": "Exact post-ID joins; primary precedence only for equivalent active counts. Historical copies never replace metric authority. Missing differs from zero; views and impressions remain separate. Observation times are source claims, not proof of recollection or growth.",
    }


def _normalize(versions):
    content, metrics = defaultdict(list), defaultdict(list)
    target_ids, orphan_ids = set(), set()
    for version in versions:
        role = version["role"]
        if role in _CONTENT_ROLES | _METRIC_ROLES:
            if not isinstance(version["record"], dict):
                raise ValueError("Selected content/metric source records must be objects")
            post_id = _post_id(version["record"])
            if role in _CONTENT_ROLES:
                content[post_id].append(version)
                if role in _ACTIVE_CONTENT:
                    if (
                        not isinstance(version["record"].get("text"), str)
                        or not version["record"]["text"].strip()
                    ):
                        raise ValueError("Primary and gapfill target records require supplied nonempty text")
                    target_ids.add(post_id)
            else:
                _counts(version)
                metrics[post_id].append(version)
                if role != "historical_metrics":
                    orphan_ids.add(post_id)
    targets = []
    for post_id in sorted(target_ids):
        source_versions = _ordered(content[post_id])
        selected = next(version for version in source_versions if version["role"] in _ACTIVE_CONTENT)
        resolution, conflicts = _field_resolution(source_versions, selected)
        metric_versions = _ordered(metrics[post_id])
        selected_metric, metric_resolution = _metric_resolution(metric_versions)
        targets.append(
            {
                "post_id": post_id,
                "selected": selected["record"],
                "selected_version_id": selected["version_id"],
                "versions": source_versions,
                "field_resolution": resolution,
                "conflicts": conflicts,
                "metric_versions": metric_versions,
                "selected_metric": selected_metric,
                "metric_resolution": metric_resolution,
            }
        )
    orphans = [
        {
            "post_id": post_id,
            "versions": _ordered(metrics[post_id]),
            "reason": "metrics_without_delivered_target_text",
            "judgeable": False,
        }
        for post_id in sorted(orphan_ids - target_ids)
    ]
    return targets, orphans


def _summary_audit(versions, targets):
    target_map = {target["post_id"]: target for target in targets}
    audits = []
    for version in versions:
        if not version["path"].endswith(".csv"):
            continue
        row = version["record"]
        available = [field for field in _COUNT_FIELDS if field in row]
        blanks = [field for field in available if row[field] == ""]
        mismatches = []
        target = target_map.get(str(row.get("post_id")))
        if target:
            selected = target["metric_resolution"]["normalized_counts"]
            for field in available:
                if row[field] != "" and str(selected[field]) != row[field].replace(",", ""):
                    mismatches.append(field)
        audits.append(
            {
                "version_id": version["version_id"],
                "path": version["path"],
                "role": version["role"],
                "line": version["line"],
                "post_id": row.get("post_id"),
                "metric_cell_count": len(available),
                "blank_metric_fields": blanks,
                "all_metric_cells_blank": bool(available) and len(blanks) == len(available),
                "nonblank_metric_mismatches": mismatches,
                "target_joined": target is not None,
            }
        )
    return {
        "authority": "Supplied CSVs are audited only; metric JSONL remains authoritative.",
        "row_count": len(audits),
        "blank_metric_cell_count": sum(len(row["blank_metric_fields"]) for row in audits),
        "all_metric_cells_blank_rows": sum(row["all_metric_cells_blank"] for row in audits),
        "primary_row_count": sum(row["role"] in {"summary", "primary_summary"} for row in audits),
        "primary_all_metric_cells_blank_rows": sum(
            row["all_metric_cells_blank"] and row["role"] in {"summary", "primary_summary"} for row in audits
        ),
        "rows": audits,
    }


def _summary_csv(targets):
    fields = [
        "post_id",
        "author_handle",
        "created_at_utc",
        "post_type",
        "has_media",
        "text",
        *_COUNT_FIELDS,
        "observed_at",
        "metric_resolution",
    ]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for target in targets:
        row = {field: target["selected"].get(field) for field in fields}
        row.update(target["metric_resolution"]["normalized_counts"])
        row.update(
            post_id=target["post_id"],
            observed_at=(target["selected_metric"] or {}).get("observed_at"),
            metric_resolution=target["metric_resolution"]["status"],
        )
        for key, value in row.items():
            if isinstance(value, str) and (
                value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))
            ):
                row[key] = "'" + value
        writer.writerow(row)
    return stream.getvalue().encode()


def read_source_archive(source: Path, destination: Path, selection: dict) -> dict:
    """Verify immutable source bytes and deterministic private normalization.

    ``selection['files']`` maps paths relative to the supplied manifest directory
    to roles. Unselected files remain inventoried as ancillary. The one permitted
    checksum exception is explicitly reported stale self-manifest metadata.
    """
    source = Path(source)
    if source.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Compressed source archive size limit exceeded")
    content = source.read_bytes()
    destination = _private_directory(Path(destination))
    archive_path = destination / "original_archive.zip"
    _write_once(archive_path, content)
    source_hash, selection_hash = _sha(content), digest(selection)
    derived_path = destination / "derived_input_manifest.json"
    if derived_path.exists():
        previous = json.loads(derived_path.read_text())
        if previous.get("source_sha256") != source_hash or previous.get("selection_hash") != selection_hash:
            raise ValueError("Immutable source archive or normalization selection changed")
        for filename, expected in previous.get("derived_artifacts", {}).items():
            if (
                filename not in {"bundle.json", "derived_summaries.csv"}
                or not (destination / filename).is_file()
                or _sha((destination / filename).read_bytes()) != expected
            ):
                raise ValueError("Immutable derived artifact integrity mismatch")
    manifest, files, versions, integrity = _read_verified(content, selection)
    targets, orphans = _normalize(versions)
    integrity["summary_audit"] = _summary_audit(versions, targets)
    bundle = {
        "adapter_version": "source_archive_v1",
        "source_id": digest({"archive_sha256": source_hash}),
        "source_sha256": source_hash,
        "archive_path": str(archive_path),
        "source_manifest": manifest,
        "selection_hash": selection_hash,
        "integrity": integrity,
        "files": files,
        "versions": versions,
        "targets": targets,
        "orphans": orphans,
    }
    outputs = {"bundle.json": _json_bytes(bundle), "derived_summaries.csv": _summary_csv(targets)}
    derived = {
        "manifest_version": "derived_source_inventory_v1",
        "source_sha256": source_hash,
        "archive_size_bytes": len(content),
        "selection_hash": selection_hash,
        "selection": selection,
        "files": files,
        "integrity": integrity,
        "derived_artifacts": {filename: _sha(body) for filename, body in outputs.items()},
        "self_hash_policy": "This derived manifest excludes its own bytes.",
    }
    for filename, body in outputs.items():
        _write_once(destination / filename, body)
    _write_once(derived_path, _json_bytes(derived))
    return bundle
