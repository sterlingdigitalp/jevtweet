"""Independent archive integration regressions using original, offline ZIP fixtures.

No source corpus, platform ID, provider call, or actual paid authorization is used.
The injected provider only exercises the application's ordinary persistence path.
"""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from test_archive_sources import content, jsonl, make_archive, metrics

from jevtweet import diagnostics
from jevtweet.archive_prepare import prepare_archive_diagnostic
from jevtweet.contracts import Candidate, Factor, ProviderResult, canonical
from jevtweet.diagnostic_report import write_diagnostic_report
from jevtweet.provider import TypeSafeProvider
from jevtweet.research_restrictions import restricted_candidates
from jevtweet.service import Service
from jevtweet.settings import Settings

COMMIT = "b" * 40
NOW = datetime(2026, 2, 10, tzinfo=timezone.utc)
SENTINEL = "COLLECTOR_METADATA_MUST_NEVER_REACH_PROVIDER"


@pytest.fixture
def archive_review_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "code_commit", lambda: COMMIT)
    monkeypatch.setattr("jevtweet.service.code_commit", lambda: COMMIT)
    monkeypatch.setattr("jevtweet.archive_prepare.now", lambda: NOW)

    async def never_live(*args, **kwargs):
        pytest.fail("Archive review is offline; a real provider must never be invoked")

    monkeypatch.setattr(TypeSafeProvider, "ask", never_live)
    leak = {
        "likes": 739,
        "views": 123456,
        "rank": 1,
        "follower_count_at_post": 10000,
        "collector_analysis": SENTINEL,
        "nested": {"views": 999, "annotation": SENTINEL},
    }
    posts = [
        content(
            "p-a",
            text="I reached 123 followers while building a compiler pottery app.",
            author_handle="invented_alpha",
            created_at_utc="2026-01-03T00:00:00Z",
            **leak,
        ),
        content("p-b", author_handle="invented_beta", created_at_utc="2026-02-01T00:00:00Z", **leak),
        content(
            "p-q",
            post_type="quote",
            quoted_id="q-reply",
            is_quote=True,
            author_handle="invented_alpha",
            created_at_utc="2026-02-01T00:00:00Z",
            **leak,
        ),
        content(
            "p-inline",
            post_type="quote",
            quoted_id="q-inline",
            is_quote=True,
            quoted_text="An original inline source about a glass compiler: 750 followers.",
            author_handle="invented_beta",
            created_at_utc="2026-01-03T00:00:00Z",
            **leak,
        ),
        content(
            "p-incomplete",
            post_type="quote",
            quoted_id="q-absent",
            is_quote=True,
            author_handle="invented_alpha",
            created_at_utc="2026-01-03T00:00:00Z",
        ),
        content(
            "p-media",
            has_media=True,
            media_type="photo",
            author_handle="invented_alpha",
            created_at_utc="2026-01-03T00:00:00Z",
        ),
        content(
            "p-article",
            post_type="article",
            is_article=True,
            author_handle="invented_beta",
            created_at_utc="2026-02-01T00:00:00Z",
        ),
    ]
    gap = content("p-gap", author_handle="invented_beta", created_at_utc="2026-02-02T00:00:00Z")
    quote = {
        "context_id": "q-reply",
        "text": "The requested quoted reply is about ceramic compiler errors.",
        "created_at_utc": "2026-01-02T00:00:00Z",
        "retrieved_at": "2026-02-09T00:00:00Z",
        "has_media": False,
        **leak,
        "parent_in_thread": {
            "post_id": "q-parent",
            "text": "The separate parent explains our kiln setup.",
            "created_at_utc": "2026-01-01T00:00:00Z",
            "has_media": False,
            **leak,
        },
        "thread_continuations": [
            {
                "post_id": "q-later",
                "text": "A later continuation is not the requested source.",
                "created_at_utc": "2026-03-01T00:00:00Z",
                **leak,
            }
        ],
    }
    payloads = {
        "primary/content.jsonl": jsonl(*posts),
        "gap/content.jsonl": jsonl(deepcopy(posts[0]), gap),
        "primary/metrics.jsonl": jsonl(
            *(metrics(post["post_id"], views=10 + i) for i, post in enumerate(posts))
        ),
        "gap/metrics.jsonl": jsonl(
            metrics("p-a", observed_at="2026-02-09T00:00:00Z"), metrics("p-gap", views=0)
        ),
        "orphans.jsonl": jsonl(metrics("orphan-only", views=None)),
        "history/content.jsonl": jsonl(
            content("p-a", text="Historical version of the compiler note."), content("historical-only")
        ),
        "context/source.jsonl": jsonl(quote),
        "summary.csv": b"post_id,views,likes\np-a,,\np-gap,,\n",
    }
    roles = [
        "primary_content",
        "gapfill_content",
        "metrics",
        "gapfill_metrics",
        "orphan_metrics",
        "historical_content",
        "quote_context",
        "ancillary",
    ]
    selection = {"files": dict(zip(payloads, roles, strict=True))}
    archive = make_archive(tmp_path / "invented.zip", payloads)
    settings = Settings(
        data_dir=tmp_path / "unused",
        account_dir=tmp_path / "isolated-account",
        spend_limit_usd=1,
        max_attempts=1,
        requests_per_minute=120,
    )
    directory = tmp_path / "pilot"
    result = prepare_archive_diagnostic(
        archive, directory, selection=selection, context_policy={}, settings=settings
    )
    return {
        "directory": directory,
        "archive": archive,
        "settings": settings,
        "selection": selection,
        "result": result,
        "source_posts": posts,
        "source_quote": quote,
        "payloads": payloads,
    }


def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def recursive_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from recursive_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from recursive_keys(item)


def test_archive_inputs_preserve_source_text_and_strip_nested_collector_metadata(archive_review_fixture):
    fixture = archive_review_fixture
    directory = fixture["directory"]
    prepared = read_lines(directory / "prepared_inputs.jsonl")
    requests = read_lines(directory / "diagnostic_requests.jsonl")
    assert len(prepared) == len(requests) == 5
    forbidden = {
        "likes",
        "views",
        "reposts",
        "quotes",
        "replies",
        "bookmarks",
        "rank",
        "follower_count_at_post",
        "collector_analysis",
        "nested",
        "starter_metrics",
        "source_id",
        "source_version_id",
    }
    for item in prepared:
        assert not forbidden.intersection(recursive_keys(item["state"]))
        assert SENTINEL not in canonical(item["state"])
        assert "A later continuation is not the requested source." not in canonical(item["state"])
    texts = [item["state"]["candidate"]["text"] for item in prepared]
    assert "I reached 123 followers while building a compiler pottery app." in texts
    quoting = [item for item in prepared if item["state"]["candidate"]["quoted"]]
    assert len(quoting) == 2
    reply = next(
        item for item in quoting if "requested quoted reply" in item["state"]["candidate"]["quoted"]["text"]
    )
    assert reply["state"]["candidate"]["quoted"]["text"] == fixture["source_quote"]["text"]
    assert reply["state"]["topic"]["text"].endswith(fixture["source_quote"]["parent_in_thread"]["text"])
    assert all(request["candidate"]["published_at"] is None for request in requests)
    assert all(request["context"]["evaluation_split"] == "development" for request in requests)
    assert all(request["context"]["historical"] is None for request in requests)
    assert {request["audience_id"] for request in requests} == {"production_ai_coding"}
    assert {request["profile_id"] for request in requests} == {"text_core_v1"}


def test_archive_denominators_keep_exclusions_orphans_and_versions_separate(archive_review_fixture):
    directory = archive_review_fixture["directory"]
    manifest = json.loads((directory / "source_manifest.json").read_text())
    panels = json.loads((directory / "panel_manifest.json").read_text())
    bundle = json.loads((directory / "source/bundle.json").read_text())
    assert len(bundle["targets"]) == manifest["record_count"] == 8
    assert len(manifest["eligible_candidate_ids"]) == 5
    assert len(manifest["excluded_candidates"]) == 3
    assert len(panels["records"]) == 8
    assert len(panels["panels"]["original"]["structural_ids"]) == 3
    assert len(panels["panels"]["quote"]["structural_ids"]) == 3
    assert [row["post_id"] for row in bundle["orphans"]] == ["orphan-only"]
    assert len([target for target in bundle["targets"] if target["post_id"] == "p-a"]) == 1
    assert len(next(target for target in bundle["targets"] if target["post_id"] == "p-a")["versions"]) == 3
    snapshots = read_lines(directory / "metric_snapshots.jsonl")
    assert len(snapshots) == 8
    assert any(snapshot["views"] == 0 for snapshot in snapshots)
    assert all(
        snapshot["observed_at"] is None and snapshot["elapsed_hours"] is None for snapshot in snapshots
    )


def test_changed_snapshot_popularity_cannot_change_cohort_order_or_provider_inputs(
    archive_review_fixture, tmp_path
):
    fixture = archive_review_fixture
    payloads = dict(fixture["payloads"])

    def replace_collector_counts(value):
        if isinstance(value, dict):
            return {
                key: 7654321
                if key in {"views", "likes", "rank", "follower_count_at_post"}
                else replace_collector_counts(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [replace_collector_counts(item) for item in value]
        return value

    for name, body in payloads.items():
        if name.endswith(".jsonl"):
            payloads[name] = jsonl(
                *(replace_collector_counts(json.loads(line)) for line in body.splitlines() if line.strip())
            )
    modified = make_archive(tmp_path / "other-popularity.zip", payloads)
    other = tmp_path / "other-pilot"
    prepare_archive_diagnostic(
        modified,
        other,
        selection=fixture["selection"],
        context_policy={},
        settings=fixture["settings"],
    )
    first_inputs = read_lines(fixture["directory"] / "prepared_inputs.jsonl")
    changed_inputs = read_lines(other / "prepared_inputs.jsonl")
    assert changed_inputs == first_inputs
    first_panels = json.loads((fixture["directory"] / "panel_manifest.json").read_text())["panels"]
    changed_panels = json.loads((other / "panel_manifest.json").read_text())["panels"]
    assert changed_panels == first_panels


def test_every_archive_identity_including_inline_source_is_permanently_development_only(
    archive_review_fixture,
):
    fixture = archive_review_fixture
    service = Service(replace(fixture["settings"], data_dir=fixture["directory"] / "store"))
    expected = [
        "p-a",
        "p-b",
        "p-q",
        "p-inline",
        "p-incomplete",
        "p-media",
        "p-article",
        "p-gap",
        "q-reply",
        "q-parent",
        "q-later",
        "q-inline",
        "orphan-only",
        "historical-only",
    ]
    candidates = [
        Candidate(candidate_id=identity, text=f"Changed independent text {identity}.")
        for identity in expected
    ]
    restricted = restricted_candidates(service.store, candidates)
    missing = [identity for identity in expected if f"{identity}:1" not in restricted]
    assert not missing, f"Archive identities must not become untouched holdouts: {missing}"


@pytest.mark.parametrize(
    "name",
    [
        "groups.json",
        "context_policy.json",
        "panel_manifest.json",
        "source/original_archive.zip",
    ],
)
def test_changed_archive_bound_inputs_block_authorization(archive_review_fixture, name):
    directory = archive_review_fixture["directory"]
    path = directory / name
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="integrity|hash"):
        diagnostics.authorize_diagnostic(
            directory,
            pilot_ceiling_usd=0.1,
            approved_by="Invented owner",
            approval_note="Synthetic test only",
        )
    assert not (directory / "authorization.json").exists()


@pytest.mark.asyncio
async def test_previous_pilot_authorization_never_authorizes_new_archive(archive_review_fixture, monkeypatch):
    fixture = archive_review_fixture
    directory = fixture["directory"]
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-offline-not-a-key")
    from jevtweet.diagnostic_contracts import DiagnosticAuthorization

    authorization = DiagnosticAuthorization(
        protocol_hash="c" * 64,
        approved_by="Invented prior owner",
        approval_note="Prior, unrelated fixture pilot",
        pilot_ceiling_usd=1,
    )
    (directory / "authorization.json").write_text(canonical(authorization) + "\n")
    with pytest.raises(ValueError, match="different protocol"):
        await diagnostics.run_diagnostic(directory, settings=fixture["settings"])
    assert not (directory / "judgment_freeze.json").exists()


class OfflineProviderEnvelope:
    """Explicit fabricated transport fixture; never interpreted as live research."""

    def __init__(self):
        self.calls = 0

    async def ask(self, state, questions):
        self.calls += 1
        factors = {}
        for key, question in questions.items():
            if question["type"] == "score":
                factors[key] = Factor(
                    question_id=key, type="score", score=0 if key == "aversion" else 2, confidence=0.8
                )
            elif question["type"] == "choice":
                factors[key] = Factor(question_id=key, type="choice", choice="assessable", confidence=0.8)
            else:
                factors[key] = Factor(question_id=key, type="noul", noul=0)
        return ProviderResult(
            model_returned="jev-1.13.0",
            factors=factors,
            usage={"input_tokens": 0},
            diagnostics={"explicit_synthetic_envelope_fixture": True},
        )


@pytest.mark.asyncio
async def test_combined_panels_prepare_authorize_freeze_report(archive_review_fixture, monkeypatch):
    fixture = archive_review_fixture
    directory = fixture["directory"]
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-offline-not-a-key")
    diagnostics.authorize_diagnostic(
        directory,
        pilot_ceiling_usd=0.1,
        approved_by="Invented owner",
        approval_note="Offline transport fixture",
    )
    provider = OfflineProviderEnvelope()
    service = Service(replace(fixture["settings"], data_dir=directory / "store"), provider=provider)
    result = await diagnostics.run_diagnostic(directory, settings=fixture["settings"], service=service)
    assert result["status"] == "frozen" and result["metrics_joined"] is False
    assert provider.calls == 5
    first = (directory / "judgment_freeze.json").read_bytes()
    report = write_diagnostic_report(directory)
    assert report["coverage"]["total_records"] == 8
    assert report["coverage"]["initial_cohort_records"] == 5
    assert report["coverage"]["excluded_records"] == 3
    assert report["coverage"]["fully_scored_records"] == 5
    assert report["source_inventory"]["target_records"] == 8
    assert report["source_inventory"]["metrics_only_orphan_records"] == 1
    assert (directory / "judgment_freeze.json").read_bytes() == first
    assert report["predictor_promoted"] is report["predictive_accuracy_established"] is False
    assert not service.store.list("outcome") and not service.store.list("predictor")
    table = read_lines(directory / "diagnostic_table.jsonl")
    assert len(table) == 8
    assert sum(row["judgment"] is not None for row in table) == 5
    assert report["associations"] == {}
    assert "No pooled panel comparison" in report["disagreements"]["rule"]
    grouped = report["grouped_diagnostics"]
    assert set(grouped) == {"panel", "account", "month", "panel_account_month"}
    for strata in grouped.values():
        assert sum(group["coverage"]["all_targets"] for group in strata) == 8
        assert sum(group["coverage"]["planned"] for group in strata) == 5
        assert sum(group["coverage"]["fully_scored"] for group in strata) == 5
        for group in strata:
            assert set(group["associations"]) == {"composite_vs_views", "direct_vs_views"}
    panels = {entry["group"]["panel"]: entry["coverage"] for entry in grouped["panel"]}
    assert panels["original"]["all_targets"] == panels["original"]["planned"] == 3
    assert panels["quote"]["all_targets"] == 3 and panels["quote"]["planned"] == 2
    assert panels["excluded"]["all_targets"] == 2 and panels["excluded"]["planned"] == 0
    accounts = {entry["group"]["account"]: entry["coverage"]["all_targets"] for entry in grouped["account"]}
    assert accounts == {"invented_alpha": 4, "invented_beta": 4}
    months = {entry["group"]["month"]: entry["coverage"]["all_targets"] for entry in grouped["month"]}
    assert months == {"2026-01": 4, "2026-02": 4}
