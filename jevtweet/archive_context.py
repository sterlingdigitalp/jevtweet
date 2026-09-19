"""Outcome-blind identity resolution for privately preserved archive versions.

This adapter makes no authenticity or historical-availability claim. Explicit
source declarations determine context, while substantive disagreements remain
quarantined until a version selection is declared. Provider evidence is rebuilt
from a narrow allowlist; raw collector dictionaries never become evidence.
"""

from __future__ import annotations

import re
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone

POLICY_VERSION = "archive_context_v1"
DEFAULT_POLICY = {
    "assessment_mode": "current_supplied_text_diagnostic",
    "unknown_publication": "observe_at_assessment",
    "unknown_media": "supplied_text_only",
    "context_path_priority": [],
    "explicit_overrides": {},
    "context_limitations": {},
}
CONTENT_ROLES = {"primary_content", "gapfill_content", "historical_content"}
CONTEXT_ROLES = {"quote_context", "thread_context", "article_context"}
ROLE_ORDER = ("quote_context", "thread_context", "primary_content", "gapfill_content", "inline_quote")
TEXT_LIMITS = {
    "missing_source_text",
    "partial_or_truncated_text",
    "article_or_url_only_context",
    "article_full_body_missing",
    "future_evidence",
    "invalid_source_timestamp",
}


def _date(value):
    if value in (None, ""):
        return None
    parsed = (
        value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None:
        raise ValueError("Archive evidence timestamps must carry a timezone")
    return parsed.astimezone(timezone.utc)


def _policy(raw):
    if not isinstance(raw, dict) or set(raw) - set(DEFAULT_POLICY):
        raise ValueError("Unsupported archive context policy fields")
    policy = deepcopy(DEFAULT_POLICY)
    policy.update(deepcopy(raw))
    for name in ("assessment_mode", "unknown_publication", "unknown_media"):
        if policy[name] != DEFAULT_POLICY[name]:
            raise ValueError(f"Unsupported archive context policy {name}")
    priorities = policy["context_path_priority"]
    if not isinstance(priorities, list) or not all(isinstance(p, str) and p for p in priorities):
        raise ValueError("Context path priority must list explicit source paths")
    if len(priorities) != len(set(priorities)):
        raise ValueError("Context path priority contains duplicate paths")
    if not isinstance(policy["explicit_overrides"], dict) or not isinstance(
        policy["context_limitations"], dict
    ):
        raise ValueError("Context overrides and limitations must be identity-keyed maps")
    for identity, override in policy["explicit_overrides"].items():
        if (
            not isinstance(identity, str)
            or not isinstance(override, dict)
            or set(override) - {"version_id", "path", "reason"}
            or not isinstance(override.get("reason"), str)
            or not override["reason"].strip()
            or (bool(override.get("version_id")) == bool(override.get("path")))
        ):
            raise ValueError("An explicit context selection requires one version/path and a written reason")
    for identity, reasons in policy["context_limitations"].items():
        if (
            not isinstance(identity, str)
            or not isinstance(reasons, list)
            or not all(
                isinstance(reason, str) and re.fullmatch(r"[a-z_]{3,80}", reason) for reason in reasons
            )
        ):
            raise ValueError("Context limitations must use explicit identity-keyed reason codes")
    return policy


def _text_limits(raw, text):
    reasons = []
    if not text.strip():
        reasons.append("missing_source_text")
    statuses = " ".join(
        str(raw.get(k) or "")
        for k in (
            "status",
            "availability",
            "text_status",
            "text_completeness",
            "quoted_text_status",
            "quoted_text_completeness",
            "article_body_status",
        )
    ).lower()
    if (
        re.search(r"partial|truncat|excerpt|opening_only", statuses)
        or re.search(r"(?:\.\.\.|…|\[truncated\])\s*$", text, re.I)
        or re.match(r"\s*(?:partial|truncated|excerpt)\s*[—:\-]", text, re.I)
    ):
        reasons.append("partial_or_truncated_text")
    if raw.get("full_body_missing") is True:
        reasons.append("article_full_body_missing")
    if (
        raw.get("is_article") is True
        or raw.get("post_type") == "article"
        or raw.get("article_url")
        or re.search(r"/i/article/", text)
        or re.fullmatch(r"(?:https?://\S+\s*)+", text.strip())
    ):
        reasons.append("article_or_url_only_context")
    return reasons


def _node(version, raw, identity, text, at, *, suffix="", role=None, relation="source", relative_to=None):
    text = text if isinstance(text, str) else ""
    reasons = _text_limits(raw, text)
    published = retrieved = None
    try:
        published = _date(raw.get("created_at_utc") or raw.get("published_at"))
        retrieved = _date(raw.get("retrieved_at") or raw.get("available_at"))
    except (ValueError, TypeError, OverflowError):
        reasons.append("invalid_source_timestamp")
    if any(timestamp and timestamp > at for timestamp in (published, retrieved)):
        reasons.append("future_evidence")
    has_media = raw.get("has_media", raw.get("has_media_on_quoted"))
    media_type = str(raw.get("media_type") or raw.get("quoted_media_type") or "").lower()
    if (
        raw.get("media_items")
        or raw.get("starter_media")
        or media_type
        in {"photo", "image", "images", "video", "audio", "gif", "animated_gif", "screenshot", "mixed"}
    ):
        has_media = True
    if has_media is True:
        reasons.append(
            "source_media_outside_text_panel"
            if raw.get("media_description") or raw.get("quoted_media_transcript")
            else "source_media_without_description"
        )
    return {
        "post_id": str(identity),
        "source_version_id": version["version_id"] + suffix,
        "archive_version_id": version["version_id"],
        "path": version["path"],
        "line": version["line"],
        "role": role or version["role"],
        "source_role": version["role"],
        "relation": relation,
        "relative_to": relative_to,
        "text": text,
        "published_at": published.isoformat() if published else None,
        "available_at": max(at, retrieved).isoformat() if retrieved else at.isoformat(),
        "timestamp_basis": "claimed_publication_current_ingestion"
        if published
        else "ingestion_observation_publication_unknown",
        "media_completeness": "declared_no_media"
        if has_media is False
        else "indicated_media_unrepresented"
        if has_media is True
        else "unknown_supplied_text_only",
        "reason_codes": sorted(set(reasons)),
        "parent_id": (raw.get("parent_in_thread") or {}).get("post_id") or raw.get("parent_id"),
    }


def _nodes(bundle, at):
    nodes = []
    unique = {v["version_id"]: v for v in bundle.get("versions", [])}
    for target in bundle.get("targets", []):
        for version in target.get("versions", []):
            if isinstance(version, dict):
                unique.setdefault(version["version_id"], version)
    for version in sorted(unique.values(), key=lambda v: (v["path"], v["line"], v["version_id"])):
        role, raw = version["role"], version["record"]
        if role not in CONTENT_ROLES | CONTEXT_ROLES:
            continue
        if role in CONTENT_ROLES:
            identity = raw.get("post_id")
        else:
            identity = (
                raw.get("context_id")
                or raw.get("quoted_id")
                or raw.get("post_id")
                or raw.get("source_post_id")
                or raw.get("article_id")
            )
        if identity:
            text = (
                raw.get("text")
                or raw.get("quoted_text")
                or raw.get("starter_text")
                or raw.get("opening")
                or raw.get("opening_snippet")
            )
            declared = (
                dict(raw, is_article=True, full_body_missing=raw.get("full_body_missing", True))
                if role == "article_context"
                else raw
            )
            nodes.append(_node(version, declared, identity, text, at))
        if role in CONTENT_ROLES and raw.get("quoted_id"):
            # Target publication/media metadata says nothing about its quoted source.
            quote = {
                "text_status": raw.get("quoted_text_status"),
                "text_completeness": raw.get("quoted_text_completeness"),
                "created_at_utc": raw.get("quoted_created_at_utc"),
                "retrieved_at": raw.get("quoted_retrieved_at") or raw.get("retrieved_at"),
                "has_media": raw.get("has_media_on_quoted"),
                "media_type": raw.get("quoted_media_type"),
                "quoted_media_transcript": raw.get("quoted_media_transcript"),
            }
            nodes.append(
                _node(
                    version,
                    quote,
                    raw["quoted_id"],
                    raw.get("quoted_text"),
                    at,
                    suffix="#quoted",
                    role="inline_quote",
                    relation="quoted_by",
                    relative_to=str(raw.get("post_id")),
                )
            )
        parent = raw.get("parent_in_thread")
        if isinstance(parent, dict) and parent.get("post_id"):
            inherited = dict(parent, retrieved_at=parent.get("retrieved_at") or raw.get("retrieved_at"))
            nodes.append(
                _node(
                    version,
                    inherited,
                    parent["post_id"],
                    parent.get("text"),
                    at,
                    suffix="#parent_in_thread",
                    relation="parent",
                    relative_to=str(identity),
                )
            )
        for field in ("continuations", "thread_continuations"):
            for index, continuation in enumerate(raw.get(field) or []):
                if not isinstance(continuation, dict) or not continuation.get("post_id"):
                    continue
                inherited = dict(
                    continuation, retrieved_at=continuation.get("retrieved_at") or raw.get("retrieved_at")
                )
                nodes.append(
                    _node(
                        version,
                        inherited,
                        continuation["post_id"],
                        continuation.get("text"),
                        at,
                        suffix=f"#{field}/{index}",
                        relation="continuation",
                        relative_to=str(identity),
                    )
                )
    return nodes


def _evidence(node):
    return {
        key: node[key]
        for key in (
            "post_id",
            "text",
            "published_at",
            "available_at",
            "timestamp_basis",
            "source_version_id",
            "media_completeness",
        )
    }


def _resolve(identity, pool, policy):
    versions = pool.get(str(identity), [])
    active = [v for v in versions if v["source_role"] != "historical_content"]
    complete = [v for v in active if not set(v["reason_codes"]) & TEXT_LIMITS]
    override = policy["explicit_overrides"].get(str(identity))
    limitations = set(policy["context_limitations"].get(str(identity), []))
    for item in active:
        limitations.update(
            reason
            for reason in item["reason_codes"]
            if reason
            in {
                "source_media_without_description",
                "source_media_outside_text_panel",
                "article_full_body_missing",
            }
        )
    selected = None
    conflict = False
    if override:
        matches = [
            v
            for v in active
            if (
                override.get("version_id") in (v["source_version_id"], v["archive_version_id"])
                if override.get("version_id")
                else v["path"] == override["path"]
            )
        ]
        if len(matches) != 1:
            raise ValueError(
                "Explicit context selection must identify exactly one version of the requested ID"
            )
        selected = matches[0]
        limitations.update(selected["reason_codes"])
    elif len({v["text"].strip() for v in complete}) > 1:
        conflict = True
        limitations.add("conflicting_substantive_source_versions")
    elif complete:
        priorities = {path: index for index, path in enumerate(policy["context_path_priority"])}
        selected = min(
            complete,
            key=lambda v: (
                priorities.get(v["path"], len(priorities)),
                ROLE_ORDER.index(v["role"]) if v["role"] in ROLE_ORDER else len(ROLE_ORDER),
                v["path"],
                v["line"],
                v["source_version_id"],
            ),
        )
        limitations.update(selected["reason_codes"])
    else:
        limitations.update(reason for item in active for reason in item["reason_codes"])
        if not active:
            limitations.add("quoted_source_not_supplied")
    if selected is None and not limitations:
        limitations.add("quoted_source_not_supplied")
    decisions = []
    for item in versions:
        chosen = selected is item
        reasons = list(item["reason_codes"])
        if not chosen:
            reasons.append(
                "historical_version_not_active_authority"
                if item["source_role"] == "historical_content"
                else "rejected_by_explicit_source_selection"
                if override
                else "conflicting_substantive_source_versions"
                if conflict and item in complete
                else "equivalent_source_version_retained"
                if item in complete
                else "incomplete_source_version_retained"
            )
        else:
            reasons.append(
                "explicit_source_version_selected"
                if override
                else "equivalent_or_only_complete_source_selected"
            )
        decisions.append(
            {
                key: item[key]
                for key in (
                    "source_version_id",
                    "archive_version_id",
                    "path",
                    "line",
                    "role",
                    "relation",
                    "published_at",
                    "available_at",
                    "media_completeness",
                )
            }
            | {"selected": chosen, "reason_codes": sorted(set(reasons))}
        )
    return {
        "requested_post_id": str(identity) if identity else None,
        "selected_source_version_id": selected["source_version_id"] if selected else None,
        "selection_rationale": override.get("reason")
        if override
        else "Exact identity; complete text agreement; declared source priority for equivalent variants only",
        "versions": decisions,
        "reason_codes": sorted(limitations),
        "status": "conflicted" if conflict else "source_incomplete" if limitations else "included",
        "selected_node": selected,
    }


def resolve_panels(bundle: dict, *, assessment_at: datetime, policy: dict) -> dict:
    """Resolve structural pools without reading metrics or selecting by outcomes."""
    at = _date(assessment_at)
    if at is None:
        raise ValueError("An explicit assessment timestamp is required")
    policy = _policy(policy)
    nodes = _nodes(bundle, at)
    pool = defaultdict(list)
    for node in nodes:
        pool[node["post_id"]].append(node)
    panels = {name: {"structural_ids": [], "eligible_ids": []} for name in ("original", "quote")}
    records = []
    for target in sorted(bundle.get("targets", []), key=lambda t: str(t["post_id"])):
        raw, identity = target["selected"], str(target["post_id"])
        article = raw.get("is_article") is True or raw.get("post_type") == "article"
        quote = raw.get("is_quote") is True or raw.get("post_type") == "quote"
        supported_type = raw.get("post_type") in ("original", "quote")
        panel = (
            "excluded"
            if raw.get("has_media") is not False or article or not supported_type
            else "quote"
            if quote
            else "original"
        )
        record = {
            "post_id": identity,
            "panel": panel,
            "status": "excluded",
            "reason_codes": [],
            "quoted_evidence": None,
            "parent_evidence": None,
            "context_resolution": {},
        }
        if panel == "excluded":
            record["reason_codes"] = [
                "target_article_outside_panels"
                if article
                else "target_media_present_or_unknown"
                if raw.get("has_media") is not False
                else "target_post_type_outside_panels"
            ]
            records.append(record)
            continue
        panels[panel]["structural_ids"].append(identity)
        target_reasons = _text_limits(raw, raw.get("text") or "")
        try:
            if _date(raw.get("created_at_utc")) and _date(raw["created_at_utc"]) > at:
                target_reasons.append("future_target")
        except (ValueError, TypeError, OverflowError):
            target_reasons.append("invalid_target_timestamp")
        conflicts = target.get("conflicts", [])
        conflict_fields = {c if isinstance(c, str) else c.get("field") for c in conflicts}
        if conflict_fields & {
            "text",
            "post_type",
            "has_media",
            "is_quote",
            "is_article",
            "quoted_id",
            "parent_id",
            "created_at_utc",
        }:
            target_reasons.append("unresolved_target_source_conflict")
        record["status"] = "included"
        if panel == "quote":
            resolved = _resolve(raw.get("quoted_id"), pool, policy)
            chosen = resolved.pop("selected_node")
            record["context_resolution"] = resolved
            record["reason_codes"].extend(resolved["reason_codes"])
            record["status"] = resolved["status"]
            if chosen and resolved["status"] == "included":
                record["quoted_evidence"] = _evidence(chosen)
            if chosen and chosen.get("parent_id"):
                parent = _resolve(chosen["parent_id"], pool, policy)
                parent_node = parent.pop("selected_node")
                record["context_resolution"]["parent"] = parent
                if parent_node and parent["status"] == "included" and resolved["status"] == "included":
                    record["parent_evidence"] = _evidence(parent_node)
                elif parent["status"] != "included":
                    record["status"] = "source_incomplete"
                    record["reason_codes"].append("quoted_reply_parent_incomplete")
            if (
                "quoted_text" in conflict_fields
                and chosen
                and chosen["role"] == "inline_quote"
                and str(raw.get("quoted_id")) not in policy["explicit_overrides"]
            ):
                record["status"] = "conflicted"
                record["reason_codes"].append("unresolved_inline_quote_conflict")
        if target_reasons:
            record["status"] = (
                "conflicted" if "unresolved_target_source_conflict" in target_reasons else "source_incomplete"
            )
            record["reason_codes"].extend(target_reasons)
        if record["status"] == "included":
            panels[panel]["eligible_ids"].append(identity)
        else:
            record["quoted_evidence"] = None
            record["parent_evidence"] = None
        record["reason_codes"] = sorted(set(record["reason_codes"]))
        records.append(record)
    inventory = [
        {
            key: node[key]
            for key in (
                "post_id",
                "source_version_id",
                "archive_version_id",
                "path",
                "line",
                "role",
                "relation",
                "relative_to",
                "published_at",
                "available_at",
                "timestamp_basis",
                "media_completeness",
                "reason_codes",
            )
        }
        | {"text_present": bool(node["text"].strip())}
        for node in nodes
    ]
    return {
        "policy_version": POLICY_VERSION,
        "policy": policy,
        "panels": panels,
        "records": records,
        "context_inventory": inventory,
    }
