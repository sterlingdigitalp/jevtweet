"""Private archive adapter into the existing, unchanged diagnostic protocol.

Only individually selected text fields cross into typed Candidate/EvidenceText.
Collector objects, nested metrics, identities, sampling ranks and author statistics
remain in bound provenance sidecars. Unknown timestamps are never publication facts:
this is an assessment of text observed now, not a pre-publication backtest.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid5

from .contracts import Candidate, EvidenceText, Media, PredictionContext, canonical, now
from .corpus import candidate_key
from .diagnostic_contracts import DiagnosticRestriction, DiagnosticSourceRecord, MetricSnapshot
from .diagnostic_ingest import _hydrate_store
from .diagnostics import (
    _clean_commit,
    _freeze_prepared_inputs,
    _json,
    _load_protocol,
    _private_directory,
    _runtime,
    _sha,
    _summary,
    _verify_additional_bindings,
    _write_once,
)
from .research_restrictions import register_diagnostic_only
from .service import Service
from .settings import Settings

ADAPTER_VERSION = "diagnostic_archive_v1"
_NAMESPACE = UUID("a65f1cb9-0976-5f9a-8acf-59a7197d605e")
_METRICS = ("views", "likes", "reposts", "quotes", "replies", "bookmarks")


def _identity(post_id):
    return str(uuid5(_NAMESPACE, "post:" + post_id))


def _evidence(node, *, parent=False):
    if node is None:
        return None
    # Explicit allowlist even if a future resolver accidentally returns raw fields.
    text = node["text"]
    if parent:
        text = "Separate parent of quoted post:\n" + text
    return EvidenceText(
        text=text,
        occurred_at=node["published_at"] or node["available_at"],
        available_at=node["available_at"],
        provenance="archive_diagnostic:" + node["timestamp_basis"],
    )


def build_archive_records(bundle, resolution, *, assessment_at: datetime):
    """Normalize all target accounting; construct provider-ready inputs by allowlist."""
    decisions = {row["post_id"]: row for row in resolution["records"]}
    if len(decisions) != len(resolution["records"]) or set(decisions) != {
        t["post_id"] for t in bundle["targets"]
    }:
        raise ValueError("Panel accounting must cover every distinct target exactly once")
    candidates, contexts, snapshots, sources, groups = [], [], [], [], {}
    for index, target in enumerate(bundle["targets"], 1):
        raw, post_id = target["selected"], target["post_id"]
        decision = decisions[post_id]
        candidate_id = _identity(post_id)
        eligible = decision["status"] == "included"
        text = raw.get("text")
        media_presence = (
            "yes" if raw.get("has_media") is True else "no" if raw.get("has_media") is False else "unknown"
        )
        post_type = raw.get("post_type", "unknown")
        published_claim = raw.get("created_at_utc")
        month = "unknown"
        if published_claim:
            try:
                month = datetime.fromisoformat(published_claim.replace("Z", "+00:00")).strftime("%Y-%m")
            except ValueError:
                pass
        groups[candidate_id] = {
            "panel": decision["panel"],
            "account": raw.get("author_handle") or "unknown",
            "month": month,
        }
        sources.append(
            DiagnosticSourceRecord(
                source_id=bundle["source_id"],
                candidate_id=candidate_id,
                source_record_index=index,
                raw={
                    "Full Text": text or "",
                    "Source Post ID": post_id,
                    "Account": groups[candidate_id]["account"],
                    "Month": month,
                    "Panel": decision["panel"],
                    "Claimed Publication": published_claim or "",
                },
                source_calendar_date=None,
                source_date_precision="unknown",
                imported_at=assessment_at,
                post_type=post_type,
                media_presence=media_presence,
                initial_cohort_eligible=eligible,
                limitations=list(decision["reason_codes"]),
            ).model_dump(mode="json")
        )
        counts = target.get("metric_resolution", {}).get(
            "normalized_counts", target.get("selected_metric") or {}
        )
        snapshots.append(
            MetricSnapshot(
                snapshot_id=str(uuid5(_NAMESPACE, f"snapshot:{bundle['source_id']}:{post_id}")),
                source_id=bundle["source_id"],
                candidate_id=candidate_id,
                imported_at=assessment_at,
                **{name: counts.get(name) for name in _METRICS},
            ).model_dump(mode="json")
        )
        # No fabricated empty candidate for a missing-text orphan or unsupported type.
        # Such targets remain in normalized_source, panel accounting and restrictions.
        if (
            not isinstance(text, str)
            or not text.strip()
            or post_type not in {"original", "reply", "quote", "thread"}
        ):
            if eligible:
                raise ValueError("Eligible target has no supported complete text candidate")
            continue
        candidate = Candidate(
            candidate_id=candidate_id,
            text=text,
            language="und",
            post_type=post_type,
            author_id=None,
            published_at=None,
            content_available_at=assessment_at,
            distribution="unknown",
            synthetic=False,
            provenance=ADAPTER_VERSION,
            quoted=_evidence(decision.get("quoted_evidence")) if eligible else None,
            media=Media(kind="none" if media_presence == "no" else "other", essential=media_presence != "no"),
        )
        context = PredictionContext(
            prediction_cutoff=assessment_at,
            evaluation_split="development",
            topic=_evidence(decision.get("parent_evidence"), parent=True) if eligible else None,
        )
        if eligible and post_type == "quote" and candidate.quoted is None:
            raise ValueError("Eligible quote must retain exact quoted evidence")
        candidates.append(candidate.model_dump(mode="json"))
        contexts.append(
            {
                "candidate_id": candidate_id,
                "candidate_key": candidate_key(candidate),
                "context": context.model_dump(mode="json"),
            }
        )
    return {
        "candidates": candidates,
        "contexts": contexts,
        "metric_snapshots": snapshots,
        "normalized_source": sources,
        "groups": groups,
    }


def _register_archive(store, bundle):
    """Include source aliases, historical versions, context and metrics-only identities."""
    identities = []
    for target in bundle["targets"]:
        for identity in (target["post_id"], _identity(target["post_id"])):
            row = {"candidate_id": identity, "candidate_version": 1}
            if isinstance(target["selected"].get("text"), str):
                row["text"] = target["selected"]["text"]
            identities.append(row)

    def visit(value):
        if isinstance(value, dict):
            primary_id = (
                value.get("post_id")
                or value.get("context_id")
                or value.get("source_post_id")
                or value.get("article_id")
            )
            identity_texts = [
                (
                    primary_id,
                    value.get("text")
                    or value.get("starter_text")
                    or value.get("opening")
                    or value.get("opening_snippet"),
                )
            ]
            # An inline quoted source is a distinct identity, never an alias for
            # its target's text. Parent pointers also retain no-text membership.
            quote_text = value.get("quoted_text")
            if not primary_id and quote_text is None:
                quote_text = value.get("text")
            identity_texts.extend(
                [
                    (value.get("quoted_id"), quote_text),
                    (value.get("parent_id"), None),
                    (value.get("wrapper_post_id"), None),
                ]
            )
            for identity, text in identity_texts:
                # JSON exporters may encode the same source identifier as an integer.
                # Match the archive/context adapters without treating booleans as IDs.
                if type(identity) is int:
                    identity = str(identity)
                if isinstance(identity, str) and identity:
                    row = {"candidate_id": identity, "candidate_version": 1}
                    if isinstance(text, str) and text.strip():
                        row["text"] = text
                    identities.append(row)
            for item in value.values():
                if isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    for version in bundle["versions"]:
        visit(version["record"])
    register_diagnostic_only(store, identities, bundle["source_id"])


def prepare_archive_diagnostic(
    source: Path,
    directory: Path,
    *,
    selection: dict,
    context_policy: dict,
    settings: Settings | None = None,
    seed: int = 20260919,
):
    from .archive_context import resolve_panels
    from .archive_sources import read_source_archive

    directory = _private_directory(directory)
    settings = replace(settings or Settings(), data_dir=directory / "store")
    commit = _clean_commit()
    if settings.max_attempts > settings.requests_per_minute:
        raise ValueError(
            "Diagnostic retry allowance must fit within the configured per-minute admission limit"
        )
    service = Service(settings)
    if (directory / "protocol.json").exists():
        protocol, _ = _load_protocol(directory)
        if (
            protocol["shuffle_seed"] != seed
            or protocol["code_commit"] != commit
            or protocol["runtime"] != _runtime(settings)
        ):
            raise ValueError(
                "Existing protocol differs in seed, code or runtime; use a new versioned directory"
            )
        if (
            _json(directory / "selection.json") != selection
            or _json(directory / "context_policy.json") != context_policy
        ):
            raise ValueError("Frozen source selection/context policy is immutable")
        if _sha(Path(source)) != protocol["source_sha256"]:
            raise ValueError("Source archive checksum differs from frozen protocol")
        return _summary(directory, protocol)
    _write_once(directory / "selection.json", selection)
    _write_once(directory / "context_policy.json", context_policy)
    _write_once(
        directory / "assessment.json",
        _json(directory / "assessment.json")
        if (directory / "assessment.json").exists()
        else {"assessment_at": now().isoformat(), "mode": "current_supplied_text_diagnostic"},
    )
    assessment_at = datetime.fromisoformat(_json(directory / "assessment.json")["assessment_at"])
    bundle = read_source_archive(Path(source), directory / "source", selection)
    resolution = resolve_panels(bundle, assessment_at=assessment_at, policy=context_policy)
    normalized = build_archive_records(bundle, resolution, assessment_at=assessment_at)
    _write_once(directory / "panel_manifest.json", resolution)
    for name in ("candidates", "contexts", "metric_snapshots", "normalized_source"):
        _write_once(
            directory / (name + ".jsonl"),
            "".join(canonical(row) + "\n" for row in normalized[name]),
            text=True,
        )
    _write_once(directory / "groups.json", normalized["groups"])
    sources = normalized["normalized_source"]
    audit = {
        "adapter_version": ADAPTER_VERSION,
        "record_count": len(sources),
        "orphan_count": len(bundle["orphans"]),
        "integrity": bundle["integrity"],
        "panels": {
            name: {key: len(value) for key, value in data.items()}
            for name, data in resolution["panels"].items()
        },
        "status_counts": dict(Counter(r["status"] for r in resolution["records"])),
        "reason_counts": dict(Counter(reason for r in resolution["records"] for reason in r["reason_codes"])),
        "timing": "Current supplied-text assessment; source timestamps are unverified claims. No fixed outcome window, author history or representative sampling inferred.",
    }
    _write_once(directory / "data_quality.json", audit)
    # Bind every original archive byte and every derived input, excluding this
    # manifest's own bytes and mutable runtime/store outputs.
    paths = [p for p in (directory / "source").rglob("*") if p.is_file()]
    names = [str(p.relative_to(directory)) for p in paths]
    names += [
        "selection.json",
        "context_policy.json",
        "assessment.json",
        "panel_manifest.json",
        "groups.json",
        "data_quality.json",
        "candidates.jsonl",
        "contexts.jsonl",
        "normalized_source.jsonl",
        "metric_snapshots.jsonl",
    ]
    manifest = {
        "schema_version": "1",
        "adapter_version": ADAPTER_VERSION,
        "source_id": bundle["source_id"],
        "source_sha256": bundle["source_sha256"],
        "imported_at": assessment_at.isoformat(),
        "record_count": len(sources),
        "restrictions": DiagnosticRestriction().model_dump(mode="json"),
        "candidate_ids": [r["candidate_id"] for r in sources],
        "eligible_candidate_ids": [r["candidate_id"] for r in sources if r["initial_cohort_eligible"]],
        "excluded_candidates": [
            {
                "candidate_id": r["candidate_id"],
                "candidate_key": r["candidate_id"] + ":1",
                "reason_codes": r["limitations"],
            }
            for r in sources
            if not r["initial_cohort_eligible"]
        ],
        "artifacts": {
            name: {"sha256": _sha(directory / name), "size_bytes": (directory / name).stat().st_size}
            for name in sorted(names)
        },
        "diagnostic_plan": {
            "version": "archive_panels_v1",
            "groups_by_candidate": normalized["groups"],
            "assessment_mode": "current_supplied_text_diagnostic",
            "metrics_only_orphan_count": len(bundle["orphans"]),
            "panels": {
                "original": "A: original/no-indicated-media, unchanged baseline",
                "quote": "B: quote/no-indicated-media with resolved supplied text context",
            },
            "analysis_plan": {
                "cohort": "Fully scored inputs within each frozen panel; never silently pool panels.",
                "associations": "Spearman continuous composite and separate direct-overall versus recorded views; separate panel, account, month and joint strata; ties/missingness/n; undefined for constants or n<3.",
                "disagreements": "Top 3 absolute average-percentile-rank differences within each panel, composite versus recorded views; candidate ID tie break.",
                "coverage": "All targets, all exclusions and metrics-only orphans; failed/partial/abstained/scored/unexecuted results retained.",
                "claims": "Descriptive development diagnosis only; no probability, accuracy, calibration, causal or promotion claim.",
            },
        },
    }
    _write_once(directory / "source_manifest.json", manifest)
    _verify_additional_bindings(directory, {"additional_bindings": manifest["artifacts"]})
    _register_archive(service.store, bundle)
    _hydrate_store(service.store, directory, manifest)
    return _freeze_prepared_inputs(directory, manifest, settings, seed, commit, service)
