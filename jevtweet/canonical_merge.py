"""Additive canonical record merge with field provenance.

Earlier sources win every conflict; blanks never overwrite; every contribution
is retained as a field version. New identities are ingested whole.
"""

from __future__ import annotations

from copy import deepcopy


def _blank(value) -> bool:
    if value is None or value == "":
        return True
    return isinstance(value, str) and not value.strip()


def merge_records(existing: dict, sources: list[tuple[str, list[dict], str]]) -> tuple[dict, dict]:
    """Merge ordered sources into canonical records keyed by id field.

    Each source is (source_name, rows, id_field). Returns (records, report).
    Records map post id -> {"fields": {...}, "versions": [...], "filled_by": {...}}.
    """
    records = deepcopy(existing)
    report: dict = {
        "sources": [],
        "new_ids": [],
        "filled_fields": [],
        "versioned": 0,
        "conflicts_kept_prior": 0,
    }
    for name, rows, id_field in sources:
        seen: set[str] = set()
        added = 0
        for row in rows:
            post_id = str(row[id_field])
            if post_id in seen:
                raise ValueError(f"Duplicate {post_id} within {name}")
            seen.add(post_id)
            is_new = post_id not in records
            record = records.setdefault(post_id, {"fields": {}, "versions": [], "filled_by": {}})
            if is_new:
                report["new_ids"].append({"post_id": post_id, "source": name})
                added += 1
            record["versions"].append(
                {"source": name, "row": {k: v for k, v in row.items() if not _blank(v)}}
            )
            report["versioned"] += 1
            for key, value in row.items():
                if key == id_field or _blank(value):
                    continue
                if key not in record["fields"]:
                    record["fields"][key] = value
                    record["filled_by"][key] = name
                    if not is_new:
                        report["filled_fields"].append({"post_id": post_id, "field": key, "source": name})
                else:
                    report["conflicts_kept_prior"] += 1
        report["sources"].append({"source": name, "rows": len(rows), "new_ids": added})
    return records, report
