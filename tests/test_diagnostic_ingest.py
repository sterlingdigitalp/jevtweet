import csv
import hashlib
import json
from datetime import datetime, timezone

import pytest

from jevtweet.diagnostic_ingest import ingest_snapshot_csv
from jevtweet.storage import Store

COLUMNS = [
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
]


def source_row(text="Try a smaller test 🧪\nThen retry, once.", **overrides):
    row = dict(
        zip(
            COLUMNS,
            [
                "1",
                "2025-02-03",
                "original",
                "invented theme",
                "Try a smaller test 🧪",
                text,
                "1,200",
                "2",
                "3",
                "4",
                "5",
                "12,345",
                "No",
                "1,224",
                str(len(text.split())),
                str(len(text)),
            ],
            strict=True,
        )
    )
    row.update(overrides)
    return row


def write_source(path, rows, columns=None):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns or COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def records(directory, name):
    return [json.loads(line) for line in (directory / name).read_text().splitlines()]


def test_lossless_archive_and_honest_snapshot_timestamps(tmp_path):
    row = source_row(**{"Bookmarks": "", "Engagement Score": ""})
    source = write_source(tmp_path / "ranked export.csv", [row])
    directory = tmp_path / "private"
    before = datetime.now(timezone.utc)
    manifest = ingest_snapshot_csv(source, directory, attribution_assumption="Unverified account")
    after = datetime.now(timezone.utc)

    assert (directory / "source.csv").read_bytes() == source.read_bytes()
    assert manifest["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest["source_filename"] == source.name
    assert manifest["attribution_assumption"] == "Unverified account"
    assert before <= datetime.fromisoformat(manifest["imported_at"]) <= after
    sidecar = records(directory, "normalized_source.jsonl")[0]
    assert sidecar["raw"] == row
    assert sidecar["source_calendar_date"] == row["Date"]
    assert sidecar["source_date_precision"] == "calendar_date_only"
    assert sidecar["source_timezone"] is None
    candidate = records(directory, "candidates.jsonl")[0]
    assert candidate["text"] == row["Full Text"]
    assert candidate["published_at"] is None
    assert candidate["content_available_at"] == manifest["imported_at"]
    assert candidate["author_id"] is None
    assert candidate["distribution"] == "unknown"
    assert candidate["language"] == "und"
    assert candidate["synthetic"] is False
    context = records(directory, "contexts.jsonl")[0]["context"]
    assert context["prediction_cutoff"] == manifest["imported_at"]
    assert context["evaluation_split"] == "development"
    assert context["historical"] is context["topic"] is None
    assert context["references"] == []
    snapshot = records(directory, "metric_snapshots.jsonl")[0]
    assert snapshot["views"] == 12345
    assert snapshot["likes"] == 1200
    assert snapshot["bookmarks"] is snapshot["supplied_engagement_score"] is None
    assert snapshot["observed_at"] is snapshot["elapsed_hours"] is None
    assert snapshot["window_status"] == "unknown"
    assert snapshot["imported_at"] == manifest["imported_at"]


def test_idempotence_verifies_archive_and_artifacts(tmp_path):
    source = write_source(tmp_path / "source.csv", [source_row()])
    directory = tmp_path / "private"
    first = ingest_snapshot_csv(source, directory)
    original = {p.name: p.read_bytes() for p in directory.iterdir()}
    assert ingest_snapshot_csv(source, directory) == first
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == original
    with (directory / "metric_snapshots.jsonl").open("a") as stream:
        stream.write("{}\n")
    with pytest.raises(ValueError, match="integrity|checksum"):
        ingest_snapshot_csv(source, directory)


def test_changed_source_is_rejected_without_overwriting_archive(tmp_path):
    source = write_source(tmp_path / "source.csv", [source_row()])
    directory = tmp_path / "private"
    ingest_snapshot_csv(source, directory)
    archived = (directory / "source.csv").read_bytes()
    write_source(source, [source_row(**{"Views": "99"})])
    with pytest.raises(ValueError, match="different|checksum|source"):
        ingest_snapshot_csv(source, directory)
    assert (directory / "source.csv").read_bytes() == archived


def test_ids_do_not_depend_on_rank_metrics_filename_or_order(tmp_path):
    first_rows = [source_row("Invented A"), source_row("Invented B", Rank="2")]
    second_rows = [
        source_row("Invented B", Rank="1", Views="999", Likes="77"),
        source_row("Invented A", Rank="2", Views="1", Likes="0"),
    ]
    for index, rows in enumerate([first_rows, second_rows]):
        source = write_source(tmp_path / f"different-{index}.csv", rows)
        ingest_snapshot_csv(source, tmp_path / f"private-{index}")
    mapping = [
        {c["text"]: c["candidate_id"] for c in records(tmp_path / f"private-{i}", "candidates.jsonl")}
        for i in range(2)
    ]
    assert mapping[0] == mapping[1]


def test_bom_crlf_and_missing_calendar_date_are_not_fabricated(tmp_path):
    row = source_row("Keep this\r\nline break, exactly. 🪁", Date="")
    source = write_source(tmp_path / "source.csv", [row])
    source.write_bytes(b"\xef\xbb\xbf" + source.read_bytes())
    directory = tmp_path / "private"
    ingest_snapshot_csv(source, directory)
    assert (directory / "source.csv").read_bytes() == source.read_bytes()
    candidate = records(directory, "candidates.jsonl")[0]
    assert candidate["text"] == row["Full Text"]
    sidecar = records(directory, "normalized_source.jsonl")[0]
    assert sidecar["source_calendar_date"] is None
    assert sidecar["source_date_precision"] == "unknown"
    assert sidecar["raw"] == row


@pytest.mark.parametrize("kind", ["duplicate_header", "missing_column", "extra_cell"])
def test_malformed_csv_is_archived_and_rejected_without_silent_column_loss(tmp_path, kind):
    source = tmp_path / "source.csv"
    if kind == "duplicate_header":
        source.write_text(",".join([*COLUMNS, "Views"]) + "\n", encoding="utf-8")
    elif kind == "missing_column":
        source.write_text(",".join(COLUMNS[:-1]) + "\n", encoding="utf-8")
    else:
        write_source(source, [source_row()])
        with source.open("a") as stream:
            stream.write(",".join(["field"] * (len(COLUMNS) + 1)) + "\n")
    directory = tmp_path / "private"
    with pytest.raises(ValueError):
        ingest_snapshot_csv(source, directory)
    assert (directory / "source.csv").read_bytes() == source.read_bytes()
    assert not (directory / "source_manifest.json").exists()


def test_media_reply_quote_unknown_and_extra_columns_preserved(tmp_path):
    rows = [
        source_row("Original", Extra="keep me"),
        source_row("Picture", **{"Has Media": "Yes", "Extra": ""}),
        source_row("Reply", **{"Post Type": "reply", "Extra": ""}),
        source_row("Quote", **{"Post Type": "quote", "Extra": ""}),
        source_row("Uncertain", **{"Has Media": "", "Extra": ""}),
    ]
    source = write_source(tmp_path / "source.csv", rows, [*COLUMNS, "Extra"])
    directory = tmp_path / "private"
    manifest = ingest_snapshot_csv(source, directory)
    assert len(manifest["candidate_ids"]) == 5
    assert len(manifest["eligible_candidate_ids"]) == 1
    assert len(manifest["excluded_candidates"]) == 4
    candidates = {c["text"]: c for c in records(directory, "candidates.jsonl")}
    assert candidates["Original"]["media"]["kind"] == "none"
    for text in ["Picture", "Uncertain"]:
        assert candidates[text]["media"] == {
            "schema_version": "1",
            "kind": "other",
            "essential": True,
            "description": None,
        }
    assert candidates["Reply"]["post_type"] == "reply"
    assert candidates["Reply"]["parent"] is None
    assert candidates["Quote"]["post_type"] == "quote"
    assert candidates["Quote"]["quoted"] is None
    sidecars = records(directory, "normalized_source.jsonl")
    assert sidecars[0]["raw"]["Extra"] == "keep me"
    assert "missing_parent_context" in sidecars[2]["limitations"]
    assert "missing_quoted_context" in sidecars[3]["limitations"]
    assert "unknown_media_presence" in sidecars[4]["limitations"]
    for sidecar in sidecars:
        restriction = sidecar["restrictions"]
        assert restriction["research_role"] == "diagnostic_only"
        assert restriction["eligible_for_calibration"] is False
        assert restriction["eligible_for_final_test"] is False
        assert restriction["eligible_for_promotion"] is False


@pytest.mark.parametrize("bad", ["-1", "1.5", "1,23", "N/A", "1e3"])
def test_invalid_counts_retain_original_archive_without_partial_store(tmp_path, bad):
    source = write_source(tmp_path / "source.csv", [source_row(), source_row("Invalid", Views=bad)])
    directory = tmp_path / "private"
    store = Store(tmp_path / "store")
    with pytest.raises(ValueError, match="Views"):
        ingest_snapshot_csv(source, directory, store=store)
    assert (directory / "source.csv").read_bytes() == source.read_bytes()
    assert not (directory / "source_manifest.json").exists()
    assert store.list("candidate") == []
    assert store.list("outcome") == []


def test_audit_recomputes_snapshot_arithmetic_counts_duplicates_and_order(tmp_path):
    rows = [
        source_row("One\nline 🧪", Views="8"),
        source_row(
            "Another, line",
            Views="10,001",
            **{
                "Engagement Score": "0",
                "Likes": "0",
                "Reposts": "0",
                "Quotes": "0",
                "Replies": "0",
                "Bookmarks": "0",
            },
        ),
        source_row(
            "Another, line",
            Views="12",
            **{
                "Engagement Score": "0",
                "Likes": "0",
                "Reposts": "0",
                "Quotes": "0",
                "Replies": "0",
                "Bookmarks": "0",
            },
        ),
    ]
    source = write_source(tmp_path / "source.csv", rows)
    directory = tmp_path / "private"
    manifest = ingest_snapshot_csv(source, directory)
    report = json.loads((directory / "data_quality.json").read_text())
    assert report["record_count"] == 3
    assert report["column_count"] == 16
    assert report["exact_duplicate_text_count"] == 1
    assert report["multiline_text_count"] == 1
    assert report["views_snapshot"] == {
        "available_count": 3,
        "missing_count": 0,
        "min": 8,
        "median": 12,
        "max": 10001,
        "at_least_10000_count": 1,
    }
    assert report["zero_supplied_engagement_score_count"] == 2
    assert report["supplied_engagement_score_descending"] is True
    assert report["views_descending"] is False
    assert report["engagement_arithmetic"]["matching_count"] == 3
    assert report["word_count_check"]["matching_count"] == 3
    assert report["character_count_check"]["matching_count"] == 3
    assert len(set(manifest["candidate_ids"])) == 3


def test_store_contains_only_candidates_contexts_diagnostic_sidecars(tmp_path, monkeypatch):
    from jevtweet import research_restrictions

    calls = []
    monkeypatch.setattr(research_restrictions, "register_diagnostic_only", lambda *args: calls.append(args))
    source = write_source(tmp_path / "source.csv", [source_row()])
    store = Store(tmp_path / "store")
    directory = tmp_path / "private"
    manifest = ingest_snapshot_csv(source, directory, store=store)
    assert len(store.list("candidate")) == 1
    assert len(store.list("context")) == 1
    assert len(store.list("diagnostic_snapshot")) == 1
    assert len(store.list("diagnostic_source_record")) == 1
    assert store.list("outcome") == store.list("judgment") == []
    assert calls and calls[0][0] is store
    assert calls[0][2] == manifest["source_id"]
    assert ingest_snapshot_csv(source, directory, store=store) == manifest
