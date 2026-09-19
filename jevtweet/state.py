"""Prediction-time allowlist. Nothing from outcomes, import filenames, or annotations is serialized."""

import json
import re
from pathlib import Path

from .contracts import Audience, Candidate, PredictionContext, Reference

CONFIG = Path(__file__).parent / "config"


def audiences() -> list[Audience]:
    return [Audience.model_validate(x) for x in json.loads((CONFIG / "audiences.json").read_text())]


def audience_by_id(audience_id: str) -> Audience:
    return next((x for x in audiences() if x.audience_id == audience_id), None) or _unknown()


def _unknown():
    raise ValueError("Unknown audience ID")


def words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def similarity(a: str, b: str) -> float:
    x, y = words(a), words(b)
    return len(x & y) / len(x | y) if x | y else 1.0


def build_state(candidate: Candidate, context: PredictionContext, audience: Audience) -> dict:
    cutoff = context.prediction_cutoff
    if candidate.content_available_at > cutoff:
        raise ValueError("Candidate content was unavailable at prediction cutoff; provide an as-of version")
    if candidate.published_at is not None and cutoff > candidate.published_at:
        raise ValueError("Pre-publication prediction cutoff must not follow publication")
    missing = []

    def evidence(item, label):
        if item is None:
            return None
        if item.available_at > cutoff or item.occurred_at > cutoff:
            missing.append("future_" + label + "_excluded")
            return None
        # Provenance/filenames never sent. Only known content and its availability facts.
        return {
            "text": item.text,
            "occurred_at": item.occurred_at.isoformat(),
            "available_at": item.available_at.isoformat(),
        }

    parent = evidence(candidate.parent, "parent")
    quoted = evidence(candidate.quoted, "quoted")
    description = evidence(candidate.media.description, "media_description")
    if candidate.post_type == "reply" and not parent:
        missing.append("essential_parent_context")
    if candidate.post_type == "quote" and not quoted:
        missing.append("essential_quoted_context")
    if candidate.media.essential and description is None:
        missing.append("essential_media_description")
    if not candidate.text.strip():
        missing.append("empty_content")
    historical = None
    h = context.historical
    if h:
        if h.available_at <= cutoff and h.observed_at <= cutoff:
            historical = {
                "followers": h.followers,
                "normal_48h_views": h.normal_48h_views,
                "baseline_sample_count": h.baseline_sample_count,
                "observed_at": h.observed_at.isoformat(),
                "available_at": h.available_at.isoformat(),
            }
        else:
            missing.append("future_author_metadata_excluded")
    refs = []
    threshold = json.loads((CONFIG / "rubric_v1.json").read_text())["policy"]["near_duplicate_jaccard"]
    for ref in context.references:
        if (
            ref.candidate_id == candidate.candidate_id
            or ref.published_at >= cutoff
            or ref.available_at > cutoff
            or ref.split == "test"
            or (candidate.thread_id and ref.thread_id == candidate.thread_id)
            or similarity(candidate.text, ref.text) >= threshold
        ):
            missing.append("unsafe_reference_excluded")
            continue
        if context.evaluation_split == "train" and ref.split != "train":
            missing.append("cross_split_reference_excluded")
            continue
        if any(similarity(r["text"], ref.text) >= threshold for r in refs):
            continue
        refs.append(
            {
                "candidate_id": ref.candidate_id,
                "candidate_version": ref.candidate_version,
                "text": ref.text,
                "published_at": ref.published_at.isoformat(),
                "available_at": ref.available_at.isoformat(),
            }
        )
    return {
        "candidate": {
            "text": candidate.text,
            "language": candidate.language,
            "post_type": candidate.post_type,
            "parent": parent,
            "quoted": quoted,
            "media": {
                "kind": candidate.media.kind,
                "essential": candidate.media.essential,
                "description": description,
            },
        },
        "audience": {
            "audience_id": audience.audience_id,
            "version": audience.version,
            "description": audience.description,
            "interests": audience.interests,
            "assumed_knowledge": audience.assumed_knowledge,
            "examples": audience.examples,
            "assumptions": audience.assumptions,
        },
        "prediction_cutoff": cutoff.isoformat(),
        "topic": evidence(context.topic, "topic"),
        "historical": historical,
        "references": refs,
        "evidence": {"missing": sorted(set(missing)), "reference_count": len(refs)},
        "preprocessing_version": "allowlist_v1",
    }


def shortlist(
    candidate: Candidate, context: PredictionContext, candidates: list[Candidate], limit: int = 5
) -> list[Reference]:
    """Keyword shortlist; availability and content must be supplied as-of. No outcomes ever read."""
    pool = []
    for c in candidates:
        if (
            c.published_at is None
            or c.content_available_at > context.prediction_cutoff
            or c.published_at >= context.prediction_cutoff
        ):
            continue
        if c.candidate_id == candidate.candidate_id or (c.thread_id and c.thread_id == candidate.thread_id):
            continue
        overlap = len(words(candidate.text) & words(c.text))
        if (
            overlap < 2
            or similarity(candidate.text, c.text)
            >= json.loads((CONFIG / "rubric_v1.json").read_text())["policy"]["near_duplicate_jaccard"]
        ):
            continue
        pool.append((overlap, c))
    pool.sort(key=lambda p: (-p[0], p[1].candidate_id))
    return [
        Reference(
            candidate_id=c.candidate_id,
            candidate_version=c.candidate_version,
            text=c.text,
            published_at=c.published_at,
            available_at=c.content_available_at,
            thread_id=c.thread_id,
            split="development",
        )
        for _, c in pool[:limit]
    ]


def select_references(
    store, candidate: Candidate, context: PredictionContext, limit: int = 5
) -> list[Reference]:
    """Select earlier relevant text, excluding all known final-test candidates."""
    split_by_key = {}
    for experiment in store.list("experiment"):
        for split, keys in experiment.get("split_manifest", {}).get("partitions", {}).items():
            for key in keys:
                # Test exclusion wins if a candidate ever appeared in a final holdout.
                if split_by_key.get(key) != "test":
                    split_by_key[key] = (
                        "test" if split == "test" else ("train" if split == "train" else "development")
                    )
    candidates = []
    for item in store.list("candidate"):
        key = f"{item['candidate_id']}:{item['candidate_version']}"
        if split_by_key.get(key) == "test":
            continue
        if context.evaluation_split == "train" and split_by_key.get(key) != "train":
            continue
        candidates.append(Candidate.model_validate(item))
    refs = shortlist(candidate, context, candidates, limit)
    for ref in refs:
        ref.split = split_by_key.get(f"{ref.candidate_id}:{ref.candidate_version}", "development")
    return refs
