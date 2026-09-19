import pytest

from jevtweet.canonical_merge import merge_records


def test_priority_blanks_versions_and_fills():
    existing = {"p1": {"fields": {"text": "old", "views": 10}, "versions": [], "filled_by": {}}}
    records, report = merge_records(
        existing,
        [
            (
                "s1",
                [{"id": "p1", "text": "", "views": 99, "likes": 3}, {"id": "p2", "text": "new", "views": 5}],
                "id",
            ),
        ],
    )
    assert records["p1"]["fields"] == {"text": "old", "views": 10, "likes": 3}
    assert records["p1"]["filled_by"]["likes"] == "s1"
    assert records["p2"]["fields"] == {"text": "new", "views": 5}
    assert report["new_ids"] == [{"post_id": "p2", "source": "s1"}]
    assert {"post_id": "p1", "field": "likes", "source": "s1"} in report["filled_fields"]
    assert report["conflicts_kept_prior"] == 1
    assert all(len(r["versions"]) >= 1 for r in records.values())


def test_duplicate_within_source_rejected():
    with pytest.raises(ValueError, match="uplicate"):
        merge_records({}, [("s1", [{"id": "p1"}, {"id": "p1"}], "id")])


def test_existing_input_not_mutated():
    existing = {"p1": {"fields": {"text": "old"}, "versions": [], "filled_by": {}}}
    merge_records(existing, [("s1", [{"id": "p1", "text": "new"}], "id")])
    assert existing["p1"]["fields"] == {"text": "old"}
