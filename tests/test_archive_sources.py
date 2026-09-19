"""Invented archive fixtures; no supplied research corpus is committed here."""

import csv
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from jevtweet.archive_sources import read_source_archive


def jsonl(*rows):
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode()


def make_archive(path, payloads, *, self_stale=True, edits=None, unlisted=None):
    manifest = {
        "version": "invented",
        "files": [
            {
                "path": name,
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
                "jsonl_rows": len([line for line in body.splitlines() if line.strip()])
                if name.endswith(".jsonl")
                else None,
            }
            for name, body in payloads.items()
        ],
    }
    if self_stale:
        manifest["files"].append(
            {"path": "MANIFEST.json", "bytes": 0, "sha256": "0" * 64, "jsonl_rows": None}
        )
    for index, update in (edits or {}).items():
        manifest["files"][index].update(update)
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in payloads.items():
            archive.writestr("source_package/" + name, body)
        archive.writestr("source_package/MANIFEST.json", json.dumps(manifest).encode())
        for name, body in (unlisted or {}).items():
            archive.writestr(name, body)
    return path


def content(post_id, **updates):
    row = {
        "post_id": post_id,
        "text": f"Invented compiler pottery note {post_id}. 🧪",
        "post_type": "original",
        "has_media": False,
        "media_count": None,
        "media_count_raw": 0,
        "media_count_status": "unknown",
        "author_id": None,
    }
    row.update(updates)
    return row


def metrics(post_id, **updates):
    row = {
        "post_id": post_id,
        "views": 10,
        "likes": 0,
        "reposts": None,
        "quotes": 0,
        "replies": 0,
        "bookmarks": 0,
        "observed_at": "2025-01-03T04:00:00Z",
        "measurement_window": "unknown",
        "metric_source": "supplied_snapshot",
    }
    row.update(updates)
    return row


@pytest.fixture
def archive_fixture(tmp_path):
    payloads = {
        "primary/content.jsonl": jsonl(content("p-a", quoted_text="First quoted statement"), content("p-b")),
        "gap/content.jsonl": jsonl(
            content("p-a", quoted_text="Different quoted statement", author_id="unverified-account"),
            content("p-c"),
        ),
        "primary/metrics.jsonl": jsonl(metrics("p-a"), metrics("p-b", views=0)),
        "gap/metrics.jsonl": jsonl(
            metrics("p-a", observed_at="2025-01-03T04:15:00Z"), metrics("p-c", views=None)
        ),
        "history/content.jsonl": jsonl(content("p-a", media_count=0), content("historical-only")),
        "history/metrics.jsonl": jsonl(metrics("p-a", views=8)),
        "orphans.jsonl": jsonl(metrics("orphan-a")),
        "context/quote.jsonl": jsonl(
            {"context_id": "context-only", "text": "Invented quoted source", "views": 999}
        ),
        "summary.csv": b"post_id,views,likes\np-a,,\np-b,,\n",
    }
    roles = [
        "primary_content",
        "gapfill_content",
        "metrics",
        "gapfill_metrics",
        "historical_content",
        "historical_metrics",
        "orphan_metrics",
        "quote_context",
        "ancillary",
    ]
    selection = {"files": dict(zip(payloads, roles, strict=True))}
    archive = make_archive(tmp_path / "input.zip", payloads)
    return archive, payloads, selection


def test_archive_is_preserved_and_only_self_manifest_mismatch_is_tolerated(tmp_path, archive_fixture):
    archive, payloads, selection = archive_fixture
    destination = tmp_path / "private"
    bundle = read_source_archive(archive, destination, selection)
    assert Path(bundle["archive_path"]).read_bytes() == archive.read_bytes()
    assert bundle["source_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert bundle["integrity"]["verified_payload_count"] == len(payloads)
    assert bundle["integrity"]["discrepancies"][0]["kind"] == "stale_self_manifest_entry"
    assert bundle["source_manifest"]["version"] == "invented"
    inventory = json.loads((destination / "derived_input_manifest.json").read_text())
    assert "derived_input_manifest.json" not in {item["path"] for item in inventory["files"]}
    first = (destination / "bundle.json").read_bytes()
    assert read_source_archive(archive, destination, selection) == bundle
    assert (destination / "bundle.json").read_bytes() == first


@pytest.mark.parametrize("field,value", [("bytes", 999), ("sha256", "0" * 64), ("jsonl_rows", 999)])
def test_other_manifest_mismatches_reject_without_rewriting_original(tmp_path, field, value):
    archive = make_archive(
        tmp_path / "bad.zip", {"posts.jsonl": jsonl(content("p-a"))}, edits={0: {field: value}}
    )
    with pytest.raises(ValueError, match="manifest|Manifest"):
        read_source_archive(archive, tmp_path / "private", {"files": {"posts.jsonl": "primary_content"}})
    assert (tmp_path / "private" / "original_archive.zip").read_bytes() == archive.read_bytes()
    assert not (tmp_path / "private" / "bundle.json").exists()


@pytest.mark.parametrize(
    "path",
    ["../escape.txt", "/absolute.txt", "source_package/../../escape.txt", "source_package\\escape.txt"],
)
def test_unsafe_zip_paths_are_rejected(tmp_path, path):
    archive = make_archive(
        tmp_path / "bad.zip", {"posts.jsonl": jsonl(content("p-a"))}, unlisted={path: b"no"}
    )
    with pytest.raises(ValueError, match="path|Path"):
        read_source_archive(archive, tmp_path / "private", {"files": {"posts.jsonl": "primary_content"}})


def test_unlisted_payload_and_unknown_selection_are_rejected(tmp_path):
    archive = make_archive(
        tmp_path / "bad.zip",
        {"posts.jsonl": jsonl(content("p-a"))},
        unlisted={"source_package/hidden.txt": b"extra"},
    )
    with pytest.raises(ValueError, match="manifest|Manifest|unlisted"):
        read_source_archive(archive, tmp_path / "private", {"files": {"posts.jsonl": "primary_content"}})
    archive = make_archive(tmp_path / "good.zip", {"posts.jsonl": jsonl(content("p-a"))})
    with pytest.raises(ValueError, match="selection|Selection|unknown"):
        read_source_archive(archive, tmp_path / "other", {"files": {"absent.jsonl": "primary_content"}})


def test_primary_gapfill_union_retains_history_and_quoted_conflicts(tmp_path, archive_fixture):
    archive, _, selection = archive_fixture
    bundle = read_source_archive(archive, tmp_path / "private", selection)
    targets = {target["post_id"]: target for target in bundle["targets"]}
    assert set(targets) == {"p-a", "p-b", "p-c"}
    target = targets["p-a"]
    assert len(target["versions"]) == 3
    assert target["selected"]["quoted_text"] == "First quoted statement"
    assert "quoted_text" in target["conflicts"]
    assert target["field_resolution"]["quoted_text"]["status"] == "unresolved_conflict"
    assert target["field_resolution"]["author_id"]["verification"] == "source_claim_unverified"
    assert target["selected"]["media_count"] is None
    assert "media_count" not in target["conflicts"]
    assert target["field_resolution"]["media_count"]["historical_difference"] is True
    assert {item["post_id"] for item in bundle["orphans"]} == {"orphan-a"}
    assert any(version["record"].get("post_id") == "historical-only" for version in bundle["versions"])
    assert any(version["record"].get("context_id") == "context-only" for version in bundle["versions"])


def test_metric_authority_preserves_claimed_times_and_null_is_not_zero(tmp_path, archive_fixture):
    archive, _, selection = archive_fixture
    destination = tmp_path / "private"
    bundle = read_source_archive(archive, destination, selection)
    targets = {target["post_id"]: target for target in bundle["targets"]}
    target = targets["p-a"]
    assert len(target["metric_versions"]) == 3
    assert target["selected_metric"]["views"] == 10
    assert target["selected_metric"]["observed_at"] == "2025-01-03T04:00:00Z"
    assert target["metric_resolution"]["status"] == "equivalent_counts_primary_precedence"
    assert target["metric_resolution"]["claims_are_independent_observations"] is False
    assert len(target["metric_resolution"]["observed_at_claims"]) == 3
    with (destination / "derived_summaries.csv").open(newline="") as stream:
        summary = {row["post_id"]: row for row in csv.DictReader(stream)}
    assert summary["p-a"]["views"] == "10"
    assert summary["p-b"]["views"] == "0"
    assert summary["p-c"]["views"] == ""
    assert bundle["integrity"]["summary_audit"]["blank_metric_cell_count"] == 4


def test_conflicting_metric_values_are_not_silently_selected(tmp_path):
    payloads = {
        "content.jsonl": jsonl(content("p-a")),
        "primary.jsonl": jsonl(metrics("p-a", views=None)),
        "gap.jsonl": jsonl(metrics("p-a", views=0)),
    }
    archive = make_archive(tmp_path / "input.zip", payloads)
    bundle = read_source_archive(
        archive,
        tmp_path / "private",
        {
            "files": {
                "content.jsonl": "primary_content",
                "primary.jsonl": "metrics",
                "gap.jsonl": "gapfill_metrics",
            }
        },
    )
    target = bundle["targets"][0]
    assert target["selected_metric"] is None
    assert target["metric_resolution"]["status"] == "unresolved_count_conflict"
    assert target["metric_resolution"]["conflicts"] == ["views"]


def test_views_and_impressions_remain_separate(tmp_path):
    payloads = {
        "content.jsonl": jsonl(content("p-a")),
        "metrics.jsonl": jsonl(metrics("p-a", views=None, impressions=50)),
    }
    archive = make_archive(tmp_path / "input.zip", payloads)
    bundle = read_source_archive(
        archive,
        tmp_path / "private",
        {"files": {"content.jsonl": "primary_content", "metrics.jsonl": "metrics"}},
    )
    target = bundle["targets"][0]
    assert target["selected_metric"]["views"] is None
    assert target["metric_resolution"]["normalized_counts"]["views"] is None
    assert target["metric_resolution"]["normalized_counts"]["impressions"] == 50


def test_selection_or_source_rewrite_cannot_reuse_existing_destination(tmp_path, archive_fixture):
    archive, _, selection = archive_fixture
    destination = tmp_path / "private"
    read_source_archive(archive, destination, selection)
    selection["files"]["gap/content.jsonl"] = "historical_content"
    with pytest.raises(ValueError, match="immutable|different|changed"):
        read_source_archive(archive, destination, selection)


def test_duplicate_and_symlink_zip_members_are_rejected(tmp_path):
    for kind in ("duplicate", "symlink"):
        archive = make_archive(tmp_path / f"{kind}.zip", {"posts.jsonl": jsonl(content("p-a"))})
        with zipfile.ZipFile(archive, "a") as zipped:
            if kind == "duplicate":
                with pytest.warns(UserWarning):
                    zipped.writestr("source_package/posts.jsonl", b"{}\n")
            else:
                member = zipfile.ZipInfo("source_package/link")
                member.create_system = 3
                member.external_attr = 0o120777 << 16
                zipped.writestr(member, b"../outside")
        with pytest.raises(ValueError, match="duplicate|symlink|path"):
            read_source_archive(archive, tmp_path / kind, {"files": {"posts.jsonl": "primary_content"}})


def test_historical_json_arrays_preserve_each_version_and_source_line(tmp_path):
    row = content("p-a")
    body = json.dumps([row, row], indent=2).encode()
    payloads = {"current.jsonl": jsonl(row), "history.json": body}
    archive = make_archive(tmp_path / "input.zip", payloads)
    selection = {"files": {"current.jsonl": "primary_content", "history.json": "historical_content"}}
    bundle = read_source_archive(archive, tmp_path / "private", selection)
    versions = bundle["targets"][0]["versions"]
    historical = [version for version in versions if version["role"] == "historical_content"]
    assert len(historical) == 2
    assert historical[0]["line"] == 2
    assert historical[1]["line"] > historical[0]["line"]
    assert [version["record_index"] for version in historical] == [1, 2]
    assert len({version["version_id"] for version in historical}) == 2
    assert all(version["record"] == row for version in historical)


@pytest.mark.parametrize(
    "setting", ["MAX_ARCHIVE_BYTES", "MAX_EXPANDED_BYTES", "MAX_FILE_BYTES", "MAX_FILES", "MAX_RECORDS"]
)
def test_archive_resource_bounds_are_enforced(tmp_path, monkeypatch, setting):
    from jevtweet import archive_sources

    archive = make_archive(tmp_path / "input.zip", {"posts.jsonl": jsonl(content("p-a"), content("p-b"))})
    monkeypatch.setattr(archive_sources, setting, 1)
    with pytest.raises(ValueError, match="limit"):
        read_source_archive(archive, tmp_path / "private", {"files": {"posts.jsonl": "primary_content"}})


@pytest.mark.parametrize("artifact", ["bundle.json", "derived_summaries.csv", "derived_input_manifest.json"])
def test_derived_artifact_tampering_is_detected(tmp_path, archive_fixture, artifact):
    archive, _, selection = archive_fixture
    destination = tmp_path / "private"
    read_source_archive(archive, destination, selection)
    path = destination / artifact
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="[Ii]mmutable|integrity"):
        read_source_archive(archive, destination, selection)
