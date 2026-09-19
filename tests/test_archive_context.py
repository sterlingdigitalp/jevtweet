"""Original invented archive records; no supplied account, post or metric data."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest

AT = datetime(2026, 1, 20, 12, tzinfo=timezone.utc)
POLICY = {
    "assessment_mode": "current_supplied_text_diagnostic",
    "unknown_publication": "observe_at_assessment",
    "unknown_media": "supplied_text_only",
    "context_path_priority": ["context/dedicated.jsonl", "context/older.jsonl"],
    "explicit_overrides": {},
}


def version(identity, raw, *, role="quote_context", path="context/dedicated.jsonl", line=1):
    return {"version_id": identity, "path": path, "line": line, "role": role, "record": raw}


def target(post_id="target-a", *, quote="source-a", **changes):
    raw = {
        "post_id": post_id,
        "text": "An invented response about a botanical notebook.",
        "post_type": "quote" if quote else "original",
        "is_quote": bool(quote),
        "is_article": False,
        "has_media": False,
        "quoted_id": quote,
        "quoted_text": None,
        "created_at_utc": "2026-01-15T12:00:00Z",
        **changes,
    }
    source = version("target-version-" + post_id, raw, role="primary_content", path="content.jsonl")
    return {
        "post_id": post_id,
        "selected": raw,
        "versions": [source],
        "field_resolution": {},
        "conflicts": [],
    }


def bundle(targets=None, sources=None):
    targets = targets or [target()]
    return {"targets": targets, "versions": [*(sources or []), *(v for t in targets for v in t["versions"])]}


def resolve(data, policy=None):
    from jevtweet.archive_context import resolve_panels

    return resolve_panels(data, assessment_at=AT, policy=policy or POLICY)


def source(text="The invented notebook records pear flowering dates.", **changes):
    return {"context_id": "source-a", "text": text, "has_media": False, **changes}


def test_structural_pools_and_eligibility_do_not_depend_on_outcomes():
    data = bundle(
        [
            target("plain", quote=None),
            target(),
            target("image", has_media=True),
            target("article", is_article=True),
        ],
        [version("source-version", source(likes=999, metrics={"views": 123}, author={"followers": 999}))],
    )
    actual = resolve(data)
    assert actual["panels"]["original"] == {"structural_ids": ["plain"], "eligible_ids": ["plain"]}
    assert actual["panels"]["quote"] == {"structural_ids": ["target-a"], "eligible_ids": ["target-a"]}
    changed = deepcopy(data)
    for v in changed["versions"]:
        v["record"].update(views=0, likes=0, rank=10000, label="negative", metrics={"views": 100000000})
    for t in changed["targets"]:
        t["selected_metric"] = {"views": 100000000, "likes": 0}
    assert resolve(changed) == actual
    evidence = next(r for r in actual["records"] if r["post_id"] == "target-a")["quoted_evidence"]
    assert set(evidence) == {
        "post_id",
        "text",
        "published_at",
        "available_at",
        "timestamp_basis",
        "source_version_id",
        "media_completeness",
    }
    assert "metrics" not in evidence and "author" not in evidence


def test_dedicated_exact_identity_supersedes_stale_unknown_stub():
    data = bundle(
        sources=[
            version(
                "old",
                {"quoted_id": "source-a", "quoted_text": None, "quoted_text_status": "unknown"},
                path="context/older.jsonl",
            ),
            version("new", source()),
        ]
    )
    record = resolve(data)["records"][0]
    assert record["status"] == "included" and record["quoted_evidence"]["source_version_id"] == "new"
    decisions = {v["source_version_id"]: v for v in record["context_resolution"]["versions"]}
    assert decisions["old"]["selected"] is False and decisions["old"]["reason_codes"]


def test_complete_conflicts_quarantine_until_explicit_source_selection():
    data = bundle(
        sources=[
            version("a", source("A concise complete sentence.")),
            version(
                "b",
                source("A different long complete sentence about invented summer flowers."),
                path="context/older.jsonl",
            ),
        ]
    )
    assert resolve(data)["records"][0]["status"] == "conflicted"
    policy = deepcopy(POLICY)
    policy["explicit_overrides"] = {
        "source-a": {"version_id": "a", "reason": "Declared source identity authority"}
    }
    result = resolve(data, policy)["records"][0]
    assert result["status"] == "included"
    assert result["quoted_evidence"]["text"] == "A concise complete sentence."


def test_nested_parent_never_replaces_requested_reply_identity():
    parent = {
        "post_id": "parent-a",
        "text": "Separate invented parent wording.",
        "created_at_utc": "2026-01-10T12:00:00Z",
    }
    data = bundle(sources=[version("reply", source("Actual quoted reply wording.", parent_in_thread=parent))])
    record = resolve(data)["records"][0]
    assert record["quoted_evidence"]["post_id"] == "source-a"
    assert record["quoted_evidence"]["text"] == "Actual quoted reply wording."
    assert record["parent_evidence"]["post_id"] == "parent-a"
    assert record["parent_evidence"]["text"] == parent["text"]
    wrong = bundle(sources=[version("parent-only", {"context_id": "parent-a", "text": parent["text"]})])
    assert resolve(wrong)["records"][0]["status"] == "source_incomplete"


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"status": "partial_from_search_card"}, "partial_or_truncated_text"),
        ({"text": "Opening of an invented post..."}, "partial_or_truncated_text"),
        ({"text": "https://example.test/i/article/123"}, "article_or_url_only_context"),
        ({"full_body_missing": True, "status": "recovered"}, "article_full_body_missing"),
        (
            {"has_media": True, "media_items": [{"url": "https://example.test/video", "type": "video"}]},
            "source_media_without_description",
        ),
    ],
)
def test_partial_article_and_media_cannot_claim_complete_context(updates, reason):
    record = resolve(bundle(sources=[version("limited", source(**updates))]))["records"][0]
    assert record["status"] == "source_incomplete"
    assert reason in record["reason_codes"]
    assert record["quoted_evidence"] is None


def test_unknown_source_time_is_explicit_current_observation_not_publication():
    record = resolve(bundle(sources=[version("observed", source())]))["records"][0]
    evidence = record["quoted_evidence"]
    assert evidence["published_at"] is None and evidence["available_at"] == AT.isoformat()
    assert evidence["timestamp_basis"] == "ingestion_observation_publication_unknown"


@pytest.mark.parametrize("field", ["created_at_utc", "retrieved_at"])
def test_future_claimed_evidence_is_excluded(field):
    record = resolve(bundle(sources=[version("future", source(**{field: "2026-01-21T00:00:00Z"}))]))[
        "records"
    ][0]
    assert record["status"] == "source_incomplete"
    assert "future_evidence" in record["reason_codes"]


def test_continuations_keep_own_identity_and_timing_without_concatenation():
    data = bundle(
        sources=[
            version(
                "thread",
                source(
                    thread_continuations=[
                        {
                            "post_id": "continuation-a",
                            "text": "Earlier continuation.",
                            "created_at_utc": "2026-01-19T00:00:00Z",
                            "metrics": {"views": 100},
                        },
                        {
                            "post_id": "continuation-b",
                            "text": "Future continuation.",
                            "created_at_utc": "2026-01-21T00:00:00Z",
                            "likes": 100,
                        },
                    ]
                ),
            )
        ]
    )
    actual = resolve(data)
    assert actual["records"][0]["quoted_evidence"]["text"] == source()["text"]
    nodes = {n["post_id"]: n for n in actual["context_inventory"]}
    assert nodes["continuation-a"]["relation"] == "continuation"
    assert "future_evidence" in nodes["continuation-b"]["reason_codes"]
    assert "metrics" not in nodes["continuation-a"] and "likes" not in nodes["continuation-b"]


def test_inline_quote_conflict_is_not_silently_resolved_by_file_order():
    row = target(quoted_text="One complete supplied sentence.")
    other = version(
        "alternative",
        dict(row["selected"], quoted_text="Another complete supplied sentence."),
        role="gapfill_content",
        path="gap.jsonl",
    )
    row["versions"].append(other)
    row["conflicts"] = ["quoted_text"]
    data = bundle([row])
    assert resolve(data)["records"][0]["status"] == "conflicted"
    data["versions"].reverse()
    assert resolve(data)["records"][0]["status"] == "conflicted"


def test_unknown_media_is_text_only_and_explicit_limitations_cannot_be_overridden():
    row = target(quoted_text="An invented self-contained release announcement.")
    result = resolve(bundle([row]))["records"][0]
    assert result["status"] == "included"
    assert result["quoted_evidence"]["media_completeness"] == "unknown_supplied_text_only"
    policy = deepcopy(POLICY)
    policy["context_limitations"] = {"source-a": ["materially_visual_context_missing"]}
    result = resolve(bundle([row]), policy)["records"][0]
    assert result["status"] == "source_incomplete"
    assert "materially_visual_context_missing" in result["reason_codes"]


def test_longer_gapfill_does_not_automatically_resolve_conflicted_partial_inline_source():
    row = target(quoted_text="An invented opening...")
    row["versions"].append(
        version(
            "gap-version",
            dict(row["selected"], quoted_text="An invented opening with a full later sentence."),
            role="gapfill_content",
            path="gap.jsonl",
        )
    )
    row["conflicts"] = ["quoted_text"]
    result = resolve(bundle([row]))["records"][0]
    assert result["status"] == "conflicted" and result["quoted_evidence"] is None


def test_reply_is_not_misclassified_as_structural_original():
    row = target("reply", quote=None, post_type="reply", parent_id="missing-parent")
    actual = resolve(bundle([row]))
    assert not actual["panels"]["original"]["structural_ids"]
    assert actual["records"][0]["status"] == "excluded"


def test_media_excluded_quote_still_preserves_separate_parent_resolution():
    data = bundle(
        sources=[
            version(
                "reply",
                source(
                    has_media=True,
                    parent_in_thread={"post_id": "parent-a", "text": "A separate parent statement."},
                ),
            )
        ]
    )
    record = resolve(data)["records"][0]
    assert record["status"] == "source_incomplete"
    assert record["context_resolution"]["parent"]["requested_post_id"] == "parent-a"
    assert record["quoted_evidence"] is None and record["parent_evidence"] is None


def test_article_opening_never_becomes_full_text_from_a_recovered_label():
    data = bundle(
        sources=[
            version(
                "article-opening",
                {"article_id": "source-a", "opening": "An invented article opening.", "status": "recovered"},
                role="article_context",
            )
        ]
    )
    record = resolve(data)["records"][0]
    assert record["status"] == "source_incomplete"
    assert "article_full_body_missing" in record["reason_codes"]


def test_indicated_quote_media_type_is_not_lost_when_boolean_is_missing():
    row = target(quoted_text="A caption for a demonstration.", quoted_media_type="video")
    record = resolve(bundle([row]))["records"][0]
    assert record["status"] == "source_incomplete"
    assert "source_media_without_description" in record["reason_codes"]


def test_authentic_numbers_in_quote_text_are_preserved_without_collector_metadata():
    text = "Our invented team planted 12 trees in 3 hours."
    record = resolve(bundle(sources=[version("numeric-text", source(text=text, views=99999))]))["records"][0]
    assert record["quoted_evidence"]["text"] == text
    assert "views" not in record["quoted_evidence"]
