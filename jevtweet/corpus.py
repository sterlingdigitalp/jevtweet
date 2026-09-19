import csv
import io
import json
from collections import Counter
from datetime import timedelta

from .contracts import Candidate, OutcomeObservation, PredictionContext, canonical, digest, now, uid
from .state import CONFIG, similarity
from .storage import ConflictError


def candidate_key(c: Candidate | dict) -> str:
    if isinstance(c, Candidate):
        return f"{c.candidate_id}:{c.candidate_version}"
    return f"{c['candidate_id']}:{c.get('candidate_version', 1)}"


def read_rows(content: str, format: str, max_rows: int = 1000):
    if len(content.encode()) > 10_000_000:
        raise ValueError("Import exceeds 10 MB limit")
    if format == "csv":
        rows = list(csv.DictReader(io.StringIO(content)))
    elif format == "jsonl":
        rows = []
        for line in content.splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    rows.append({"_parse_error": "Invalid JSON row"})
    else:
        raise ValueError("Expected csv or jsonl")
    if len(rows) > max_rows:
        raise ValueError(f"Import exceeds {max_rows} row cap")
    return rows


def import_candidates(store, content, format="jsonl", mapping=None, preview=False, max_rows=1000):
    raw = read_rows(content, format, max_rows)
    batch_id = uid()
    accepted = []
    errors = []
    contexts = {}
    seen = {}
    unknown = set()
    for n, row in enumerate(raw, 1):
        try:
            if not isinstance(row, dict):
                raise ValueError("Row must be an object")
            if "_parse_error" in row:
                raise ValueError(row["_parse_error"])
            mapped = (
                {
                    next((canonical for canonical, source in mapping.items() if source == k), k): v
                    for k, v in row.items()
                }
                if mapping
                else row
            )
            cdata = dict(mapped.get("candidate", mapped))
            ctxdata = mapped.get("context")
            unknown.update(set(cdata) - set(Candidate.model_fields) - {"context"})
            cdata = {k: v for k, v in cdata.items() if k in Candidate.model_fields and v != ""}
            for k in ["media", "parent", "quoted"]:
                if isinstance(cdata.get(k), str):
                    cdata[k] = json.loads(cdata[k])
            # Historical imports must assert when content was available; publication is a conservative default.
            if "candidate_id" not in cdata:
                cdata["candidate_id"] = "import-" + digest(cdata)[:24]
            existing = store.get("candidate", f"{cdata['candidate_id']}:{cdata.get('candidate_version', 1)}")
            if "content_available_at" not in cdata:
                if cdata.get("published_at"):
                    cdata["content_available_at"] = cdata["published_at"]
                elif existing:
                    cdata["content_available_at"] = existing["content_available_at"]
            c = Candidate.model_validate(cdata)
            if not c.text.strip():
                raise ValueError("Candidate text is empty")
            context = (
                PredictionContext.model_validate(ctxdata)
                if ctxdata
                else PredictionContext(prediction_cutoff=c.published_at or c.content_available_at)
            )
            from .state import audiences, build_state

            build_state(c, context, audiences()[0])
            key = candidate_key(c)
            body = c.model_dump(mode="json")
            old = seen.get(key) or store.get("candidate", key)
            if old and old != body:
                raise ConflictError("Conflicting candidate/version; create a new immutable version")
            if key in seen:
                raise ValueError("Duplicate ID/version within import")
            seen[key] = body
            if not preview:
                store.put("candidate", key, body)
                store.put("context", key, context)
            accepted.append(body)
            contexts[key] = context.model_dump(mode="json")
        except (ValueError, TypeError) as e:
            errors.append({"row": n, "error": str(e)})
    report = {
        "import_id": batch_id,
        "accepted": len(accepted),
        "errors": errors,
        "preview": accepted[:50],
        "quality": quality_report(accepted),
        "unknown_fields_excluded": sorted(unknown),
        "preview_only": preview,
    }
    if not preview:
        store.put("import", batch_id, {"report": report, "original_rows": raw, "contexts": contexts})
    return report


def quality_report(candidates, outcomes=None):
    n = len(candidates)
    clusters = []
    duplicate = []
    threshold = json.loads((CONFIG / "rubric_v1.json").read_text())["policy"]["near_duplicate_jaccard"]
    # Bounded import size permits an inspectable quadratic near-duplicate pass.
    for i, c in enumerate(candidates):
        for d in candidates[:i]:
            s = similarity(c["text"], d["text"])
            if s >= threshold:
                pair = [candidate_key(d), candidate_key(c)]
                clusters.append(pair)
                if c["text"].strip() == d["text"].strip():
                    duplicate.append(pair)
    authors = Counter(c.get("author_id") or "unknown" for c in candidates)
    obs = outcomes or []
    observed = {f"{o['candidate_id']}:{o.get('candidate_version', 1)}" for o in obs}
    return {
        "rows": n,
        "duplicates": duplicate,
        "near_duplicate_pairs": clusters,
        "thread_clusters": dict(Counter(c.get("thread_id") for c in candidates if c.get("thread_id"))),
        "missing_context": [
            candidate_key(c)
            for c in candidates
            if (c.get("post_type") == "reply" and not c.get("parent"))
            or (c.get("post_type") == "quote" and not c.get("quoted"))
            or (c.get("media", {}).get("essential") and not c.get("media", {}).get("description"))
        ],
        "missing_outcomes": sum(candidate_key(c) not in observed for c in candidates),
        "suspicious_measurement_windows": [
            o.get("observation_id") for o in obs if abs(o["elapsed_hours"] - 48) > 1
        ],
        "author_counts": dict(authors),
        "largest_author_share": max(authors.values()) / n if n else None,
        "languages": dict(Counter(c.get("language", "unknown") for c in candidates)),
        "niches": dict(Counter(c.get("niche", "unknown") for c in candidates)),
        "sampling_provenance": dict(Counter(c.get("provenance", "unknown") for c in candidates)),
        "class_balance": "Not established until eligible temporal outcomes are derived",
        "limitations": [
            "Retrospective famous posts may have been encountered in model training.",
            "Winners-only/balanced corpora cannot establish deployment probabilities.",
        ],
    }


def attach_outcome(store, outcome: OutcomeObservation):
    c = store.get("candidate", f"{outcome.candidate_id}:{outcome.candidate_version}")
    if c is None:
        raise ValueError("Unknown candidate/version")
    candidate = Candidate.model_validate(c)
    if candidate.published_at is None:
        raise ValueError("Publication timestamp required to attach outcomes")
    if outcome.available_at < outcome.observed_at:
        raise ValueError("Observation availability cannot precede observation")
    expected = candidate.published_at + timedelta(hours=outcome.elapsed_hours)
    if abs((outcome.observed_at - expected).total_seconds()) > 60:
        raise ValueError("Elapsed window conflicts with publication/observation timestamps")
    if outcome.synthetic != candidate.synthetic:
        raise ValueError("Synthetic and real records cannot mix")
    # Semantic identity and insertion share one write transaction; concurrent measurements cannot race.
    body = outcome.model_dump(mode="json")
    with store.transaction() as db:
        rows = db.execute("SELECT body FROM records WHERE kind='outcome'").fetchall()
        for row in rows:
            old = json.loads(row[0])
            existing = OutcomeObservation.model_validate(old)
            same = (
                existing.candidate_id,
                existing.candidate_version,
                existing.source,
                existing.metric,
                existing.observed_at,
            ) == (
                outcome.candidate_id,
                outcome.candidate_version,
                outcome.source,
                outcome.metric,
                outcome.observed_at,
            )
            if same:
                if existing.model_dump(exclude={"observation_id"}) != outcome.model_dump(
                    exclude={"observation_id"}
                ):
                    raise ConflictError("Conflicting measurement identity")
                return old
            if existing.observation_id == outcome.observation_id:
                raise ConflictError("Conflicting observation ID")
        db.execute(
            "INSERT INTO records(kind,id,body,created_at) VALUES('outcome',?,?,?)",
            (outcome.observation_id, canonical(body), now().isoformat()),
        )
    return body


def export_results(store, format="jsonl", **filters):
    rows = [r for r in store.list("judgment") if all(not v or r.get(k) == v for k, v in filters.items())]
    groups = {
        (r["profile_id"], r["audience_id"], r["audience_version"], r["execution_mode"], r["rubric_version"])
        for r in rows
    }
    if len(groups) > 1:
        raise ValueError(
            "Export ranking requires one profile, audience/version, rubric/version, and execution mode; apply filters"
        )
    rows.sort(key=lambda r: (r["score_continuous"] is None, -(r["score_continuous"] or 0), r["judgment_id"]))
    if format == "jsonl":
        return "\n".join(json.dumps(r, ensure_ascii=False, allow_nan=False) for r in rows) + (
            "\n" if rows else ""
        )
    if format != "csv":
        raise ValueError("Expected csv or jsonl")
    fields = [
        "judgment_id",
        "candidate_id",
        "candidate_version",
        "text",
        "status",
        "execution_mode",
        "profile_id",
        "audience_id",
        "audience_version",
        "rubric_version",
        "model_requested",
        "model_returned",
        "input_hash",
        "reference_set_hash",
        "score_continuous",
        "score_1_to_5",
        "breakout_probability",
        "review_flags",
    ]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields)
    writer.writeheader()
    for r in rows:
        c = store.get("candidate", f"{r['candidate_id']}:{r['candidate_version']}") or {}
        row = {k: r.get(k) for k in fields}
        row["text"] = c.get("text", "")
        row["review_flags"] = "; ".join(r["review_flags"])
        for k, v in row.items():
            if isinstance(v, str) and (
                v.lstrip().startswith(("=", "+", "-", "@")) or v.startswith(("\t", "\r", "\n"))
            ):
                row[k] = "'" + v
        writer.writerow(row)
    return out.getvalue()
