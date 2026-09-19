"""Offline diagnostic preparation and explicitly authorized first-judgment execution.

Metrics are read during source auditing and, separately, by diagnostic_report after
an immutable result freeze. They are never read by the judgment runner.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import re
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from .contracts import Candidate, JudgeRequest, Judgment, PredictionContext, canonical, digest, now, uid
from .diagnostic_contracts import DiagnosticAuthorization
from .diagnostic_ingest import ingest_snapshot_csv
from .request_budget import request_budget
from .service import Service, code_commit
from .settings import Settings

PROTOCOL_VERSION = "diagnostic_v1"
AUDIENCE_ID = "production_ai_coding"
PROFILE_ID = "text_core_v1"

COLLECTION_REQUIREMENTS = """# Data collection requirements

This snapshot source cannot support the current breakout-readiness task. It has no
verified publication timestamps/time zones, measurement timestamps or 48-hour
observation provenance, no qualifying as-of author history, and no declared complete
or outcome-independent sampling frame. Unknown distribution is not organic evidence.
Filename attribution is an assumption, not verified authorship. Recorded views are
not impressions and neither may substitute for a missing observation window.

Collect verified platform post/author identities; publication times with time zones;
metric source, observation and availability times; separately defined 48-hour views;
distribution provenance; prediction-time parent, quoted-post and media context; and
qualifying earlier author history. Declare a complete collection window or an
outcome-independent sample including ordinary posts. Include independent authors
when the intended claim extends beyond one account. Preserve a genuinely later,
untouched/prospective cohort. This diagnostic source remains permanently ineligible
for calibration, final tests and promotion, even if more metadata becomes available.

Only then use development readiness to estimate event frequency, exclusions,
positive/negative support and independent author support. Do not estimate deployment
breakout rates or normal-author baselines from these unwindowed snapshots. The
5,000-row evaluation cap and all promotion thresholds are unchanged. If rare-event
support exceeds that cap, decide and version any resource-policy change before the
future final experiment, without inspecting its outcomes to tune collection.
"""


def _private_directory(directory: Path) -> Path:
    directory = Path(directory).expanduser().resolve()
    root = Path(__file__).resolve().parent.parent
    if directory.is_relative_to(root) and not directory.is_relative_to(root / "private"):
        raise ValueError("Diagnostic artifacts inside this repository must be under private/")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    return directory


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path):
    return json.loads(path.read_text())


def _lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write_once(path: Path, value, *, text=False):
    """Atomic publish without replacement; interrupted writes cannot become a freeze."""
    body = value if text else canonical(value) + "\n"
    if path.exists():
        if path.read_text() != body:
            raise ValueError(f"Immutable diagnostic artifact differs: {path.name}")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", delete=False) as f:
        temporary = Path(f.name)
        try:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def _runtime(settings):
    return {
        key: getattr(settings, key)
        for key in (
            "model",
            "max_attempts",
            "input_price_per_million",
            "price_as_of",
            "max_request_tokens",
            "max_state_question_tokens",
            "timeout_seconds",
            "requests_per_minute",
            "concurrency",
        )
    }


def _clean_commit():
    commit = code_commit()
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("Diagnostic protocol requires a clean committed code version")
    return commit


def _load_protocol(directory):
    path = directory / "protocol.json"
    if not path.exists():
        raise ValueError("Prepared diagnostic protocol is required")
    protocol = _json(path)
    body = {k: v for k, v in protocol.items() if k != "protocol_hash"}
    if protocol.get("protocol_hash") != digest(body) or protocol.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("Diagnostic protocol integrity hash mismatch")
    files = {
        "source_manifest.json": "source_manifest_sha256",
        "diagnostic_requests.jsonl": "requests_sha256",
        "prepared_inputs.jsonl": "prepared_inputs_sha256",
    }
    for name, key in files.items():
        if not (directory / name).is_file() or _sha(directory / name) != protocol.get(key):
            raise ValueError(f"Diagnostic artifact integrity hash mismatch: {name}")
    _verify_additional_bindings(directory, protocol)
    requests = [JudgeRequest.model_validate(row) for row in _lines(directory / "diagnostic_requests.jsonl")]
    if [r.candidate.candidate_id for r in requests] != protocol["candidate_ids"]:
        raise ValueError("Request membership/order differs from protocol")
    return protocol, requests


def _verify_additional_bindings(directory, protocol):
    """Byte verification only; never parse outcomes during execution admission."""
    for name, expected in protocol.get("additional_bindings", {}).items():
        relative = Path(name)
        path = directory / relative
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or path.is_symlink()
            or not path.resolve().is_relative_to(directory.resolve())
            or not path.is_file()
        ):
            raise ValueError("Invalid diagnostic artifact binding path")
        if expected != {"sha256": _sha(path), "size_bytes": path.stat().st_size}:
            raise ValueError(f"Diagnostic artifact integrity hash mismatch: {name}")


def _summary(directory, protocol):
    return {
        "status": "prepared_awaiting_authorization",
        "directory": str(directory),
        "protocol_hash": protocol["protocol_hash"],
        "code_commit": protocol["code_commit"],
        "imported_records": protocol["imported_records"],
        "initial_cohort_records": len(protocol["candidate_ids"]),
        "excluded_records": len(protocol["excluded_candidates"]),
        "estimate": protocol["estimate"],
        "pilot_authorized_usd": 0,
        "live_requests_made_by_preparation": 0,
    }


def prepare_diagnostic(
    source: Path,
    directory: Path,
    *,
    settings: Settings | None = None,
    seed: int = 20260918,
    attribution_assumption: str | None = None,
):
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
        ingest_snapshot_csv(
            source, directory, store=service.store, attribution_assumption=attribution_assumption
        )
        return _summary(directory, protocol)
    manifest = ingest_snapshot_csv(
        source, directory, store=service.store, attribution_assumption=attribution_assumption
    )
    return _freeze_prepared_inputs(directory, manifest, settings, seed, commit, service)


def _freeze_prepared_inputs(directory, manifest, settings, seed, commit, service):
    """One baseline, request builder, reservation estimate and freeze for every adapter."""
    candidates = {
        r["candidate_id"]: Candidate.model_validate(r) for r in _lines(directory / "candidates.jsonl")
    }
    contexts = {
        r["candidate_id"]: PredictionContext.model_validate(r["context"])
        for r in _lines(directory / "contexts.jsonl")
    }
    ids = sorted(manifest["eligible_candidate_ids"])
    random.Random(seed).shuffle(ids)
    if not ids:
        raise ValueError("No eligible inputs qualify for the diagnostic cohort")
    requests, inputs, budgets = [], [], []
    for candidate_id in ids:
        request = JudgeRequest(
            candidate=candidates[candidate_id],
            context=contexts[candidate_id],
            audience_id=AUDIENCE_ID,
            profile_id=PROFILE_ID,
            execution_mode="live",
        )
        audience, state, questions, rubric, fingerprint = service._prepare(request)
        budget = request_budget(state, questions, settings)
        if not budget["within_limits"]:
            raise ValueError("A diagnostic request exceeds the configured request limits")
        requests.append(request.model_dump(mode="json"))
        inputs.append(
            {"candidate_id": candidate_id, "state": state, "questions": questions, "input_hash": fingerprint}
        )
        budgets.append({"candidate_id": candidate_id, "question_count": len(questions), **budget})
    _write_once(
        directory / "diagnostic_requests.jsonl", "".join(canonical(r) + "\n" for r in requests), text=True
    )
    _write_once(directory / "prepared_inputs.jsonl", "".join(canonical(r) + "\n" for r in inputs), text=True)
    initial = sum(b["per_attempt_usd"] for b in budgets)
    maximum = sum(b["per_attempt_usd"] * settings.max_attempts for b in budgets)
    account = service.account_store.spending()
    estimate = {
        "logical_requests": len(ids),
        "maximum_provider_attempts": len(ids) * settings.max_attempts,
        "max_attempts_per_request": settings.max_attempts,
        "initial_reserved_input_tokens": sum(b["reserved_input_tokens"] for b in budgets),
        "initial_reserved_usd": initial,
        "maximum_reserved_usd": maximum,
        "input_price_per_million_usd": settings.input_price_per_million,
        "price_as_of": settings.price_as_of,
        "output_price_usd": 0,
        "model": settings.model,
        "sdk_version": "0.7.0",
        "per_request": budgets,
        "account_charged_or_reserved_usd_at_preparation": account["charged_or_reserved_usd"],
        "configured_account_ceiling_usd_at_preparation": settings.spend_limit_usd,
        "account_ceiling_needed_for_full_reservation_usd": account["charged_or_reserved_usd"] + maximum,
        "limitations": "Prospective conservative application reservations, not an invoice guarantee. "
        "Actual input-token usage reconciles successful responses; unknown attempts retain "
        "reservations. Shared-account and separate pilot ceilings both apply. "
        "Preparation authorizes no spending.",
    }
    protocol = {
        "protocol_version": PROTOCOL_VERSION,
        "prepared_at": now().isoformat(),
        "code_commit": commit,
        "source_id": manifest["source_id"],
        "source_manifest_sha256": _sha(directory / "source_manifest.json"),
        "source_sha256": manifest["source_sha256"],
        "imported_records": manifest["record_count"],
        "candidate_ids": ids,
        "excluded_candidates": manifest["excluded_candidates"],
        "requests_file": "diagnostic_requests.jsonl",
        "requests_sha256": _sha(directory / "diagnostic_requests.jsonl"),
        "prepared_inputs_sha256": _sha(directory / "prepared_inputs.jsonl"),
        "shuffle_seed": seed,
        "ordering_policy": "opaque_id_sort_then_seeded_shuffle_v1",
        "audience": {
            "audience_id": audience.audience_id,
            "version": audience.version,
            "snapshot": audience.model_dump(mode="json"),
            "hash": digest(audience),
        },
        "profile_id": PROFILE_ID,
        "rubric_version": rubric["version"],
        "rubric_hash": digest(rubric),
        "model": settings.model,
        "sdk_version": "0.7.0",
        "runtime": _runtime(settings),
        "approved_spending_limit_usd": 0,
        "restrictions": manifest["restrictions"],
        "estimate": estimate,
        "retry_policy": "first_service_invocation_v1: fixed order; only bounded transport retries within "
        "Service. Persist first terminal result of every status. Never rerun for score, "
        "popularity or missingness. Resume only unstarted candidates or recover the sole "
        "persisted result of a checkpointed call. Ambiguous interrupted calls stop for review.",
        "analysis_plan": {
            "cohort": "fully scored initial original/no-indicated-media cohort only",
            "associations": "Spearman continuous composite and separate direct-overall versus recorded views and supplied engagement score; ties/missingness/n; undefined for constants or n<3",
            "disagreements": "top 3 absolute average-percentile-rank differences, composite versus recorded views; candidate ID tie break",
            "coverage": "all imported and all initial cohort; every failed/partial/abstained/scored/unexecuted result",
            "claims": "descriptive development diagnosis only; no probability, accuracy, calibration, causal or promotion claim",
        },
    }
    if manifest.get("diagnostic_plan"):
        protocol["diagnostic_plan"] = manifest["diagnostic_plan"]
        protocol["analysis_plan"] = manifest["diagnostic_plan"]["analysis_plan"]
        protocol["additional_bindings"] = manifest["artifacts"]
    protocol["protocol_hash"] = digest(protocol)
    _write_once(directory / "protocol.json", protocol)
    _write_once(directory / "collection_requirements.md", COLLECTION_REQUIREMENTS, text=True)
    audit = _json(directory / "data_quality.json")
    handoff = (
        "# Offline diagnostic preparation\n\nNo judgments have been executed. Pilot authorization is zero; "
        "the prior account verification ceiling does not authorize this pilot. Baseline audience, "
        "rubric, scoring, promotion thresholds and holdout protections are unchanged.\n\n"
        f"Protocol: `{protocol['protocol_hash']}`\nCode: `{commit}`\n\n"
        f"Imported {manifest['record_count']} records; prepared {len(ids)} independent requests; "
        f"preserved {len(manifest['excluded_candidates'])} other records with explicit limitations.\n\n"
        f"Initial reservation estimate: ${initial:.8f}; all allowed attempts: ${maximum:.8f} "
        f"({len(ids)} requests, at most {len(ids) * settings.max_attempts} provider attempts). "
        "These are conservative application estimates, not observed charges.\n\n"
        "`diagnostic_requests.jsonl` contains outcome-blind Service requests; `prepared_inputs.jsonl` "
        "contains inspectable provider states/questions. Source metrics are separate. Source timing claims "
        "are not verified publication/observation windows; author history and sampling remain unknown.\n\n"
        "After separate owner approval, bind it using `jevtweet diagnostic-authorize DIRECTORY "
        "--budget-usd APPROVED_CEILING --approved-by OWNER --note APPROVAL_REFERENCE`, then run "
        "`jevtweet diagnostic-run DIRECTORY` with the documented credential and shared-account "
        "ceiling configured. Run `jevtweet diagnostic-report DIRECTORY` only after the result freeze. "
        "No such command was executed during preparation.\n\n"
        "Current breakout-readiness is unavailable; see collection_requirements.md. "
        "No associations or research findings exist before frozen live judgments.\n\n"
        "## Recomputed private source audit\n\n```json\n"
        + json.dumps(audit, indent=2, ensure_ascii=False)
        + "\n```\n"
    )
    _write_once(directory / "OFFLINE_HANDOFF.md", handoff, text=True)
    return _summary(directory, protocol)


def authorize_diagnostic(directory: Path, *, pilot_ceiling_usd: float, approved_by: str, approval_note: str):
    directory = _private_directory(directory)
    protocol, _ = _load_protocol(directory)
    authorization = DiagnosticAuthorization(
        protocol_hash=protocol["protocol_hash"],
        approved_by=approved_by,
        approval_note=approval_note,
        pilot_ceiling_usd=pilot_ceiling_usd,
    )
    if (directory / "authorization.json").exists():
        existing = DiagnosticAuthorization.model_validate(_json(directory / "authorization.json"))
        if existing.model_dump(exclude={"approved_at"}) != authorization.model_dump(exclude={"approved_at"}):
            raise ValueError("Existing diagnostic authorization is immutable")
        return existing.model_dump(mode="json")
    _write_once(directory / "authorization.json", authorization.model_dump(mode="json"))
    return authorization.model_dump(mode="json")


def _authorization(directory, protocol):
    if not (directory / "authorization.json").is_file():
        raise ValueError(
            "Separate owner authorization bound to this protocol is required; prior account ceilings do not authorize a pilot"
        )
    authorization = DiagnosticAuthorization.model_validate(_json(directory / "authorization.json"))
    if authorization.protocol_hash != protocol["protocol_hash"]:
        raise ValueError("Diagnostic authorization is for a different protocol")
    if authorization.approved_at > now():
        raise ValueError("Diagnostic authorization timestamp is in the future")
    return authorization


def _validate_result(result, request, protocol, fingerprint):
    if (
        result.candidate_id != request.candidate.candidate_id
        or result.candidate_version != request.candidate.candidate_version
        or result.execution_mode != "live"
        or result.input_hash != fingerprint
        or result.model_requested != protocol["model"]
        or result.sdk_version != protocol["sdk_version"]
        or result.profile_id != protocol["profile_id"]
        or result.audience_id != protocol["audience"]["audience_id"]
        or result.audience_version != protocol["audience"]["version"]
        or result.rubric_version != protocol["rubric_version"]
        or result.provenance.get("rubric_hash") != protocol["rubric_hash"]
        or result.provenance.get("provider") != "typesafe_sdk"
        or result.code_commit != protocol["code_commit"]
    ):
        raise ValueError("First judgment lineage differs from frozen diagnostic protocol; no metrics join")
    no_answer_failure = (
        result.model_returned is None
        and not result.factors
        and result.status == "failed"
        and bool(result.error_category)
        and result.score_continuous is None
        and result.score_1_to_5 is None
    )
    if result.model_returned != protocol["model"] and not no_answer_failure:
        raise ValueError("First judgment has incompatible returned model identity; no metrics join")


def _validate_freeze(freeze, protocol, authorization):
    if (
        freeze.get("freeze_hash") != digest({k: v for k, v in freeze.items() if k != "freeze_hash"})
        or freeze.get("protocol_hash") != protocol["protocol_hash"]
        or freeze.get("authorization_hash") != digest(authorization)
    ):
        raise ValueError("Existing judgment freeze integrity mismatch")


async def _wait_account_capacity(service):
    """Pace before checkpointing, without reserving or consuming a first attempt.

    Shared admission still decides atomically in Service. An unrelated process can
    race this check; its resulting explicit operational failure is retained.
    """
    required = min(service.settings.max_attempts, service.settings.requests_per_minute)
    waited = 0.0
    while True:
        with service.account_store.connect() as db:
            recent = db.execute(
                "SELECT created_epoch FROM spending WHERE created_epoch>? ORDER BY created_epoch",
                (time.time() - 60,),
            ).fetchall()
        if len(recent) + required <= service.settings.requests_per_minute:
            return
        if waited >= 120:
            raise ValueError(
                "Shared-account rate capacity stayed busy; resume unstarted diagnostic requests later"
            )
        delay = max(0.05, min(5, recent[0][0] + 60.01 - time.time()))
        await asyncio.sleep(delay)
        waited += delay


async def run_diagnostic(
    directory: Path, *, settings: Settings | None = None, service: Service | None = None
):
    """Never accesses metrics; freezes every first result before a separate report command."""
    directory = _private_directory(directory)
    protocol, requests = _load_protocol(directory)
    authorization = _authorization(directory, protocol)
    if (directory / "judgment_freeze.json").exists():
        freeze = _json(directory / "judgment_freeze.json")
        _validate_freeze(freeze, protocol, authorization)
        return {"status": "already_frozen", "freeze_hash": freeze["freeze_hash"], "requests_made": 0}
    settings = replace(settings or Settings(), data_dir=directory / "store")
    if _clean_commit() != protocol["code_commit"] or _runtime(settings) != protocol["runtime"]:
        raise ValueError("Current code/runtime differs from frozen protocol")
    if not os.getenv("TYPESAFE_API_KEY") or settings.spend_limit_usd <= 0:
        raise ValueError(
            "Live diagnostic requires documented credentials and a positive shared-account ceiling"
        )
    service = service or Service(settings)
    if service.settings != settings:
        raise ValueError("Diagnostic service settings must match the frozen runtime and private store")
    prepared = _lines(directory / "prepared_inputs.jsonl")
    if len(prepared) != len(requests):
        raise ValueError("Prepared input membership mismatch")
    for request, item in zip(requests, prepared, strict=True):
        audience, state, questions, rubric, fingerprint = service._prepare(request)
        if (
            request.execution_mode != "live"
            or request.profile_id != PROFILE_ID
            or request.audience_id != AUDIENCE_ID
            or request.context.evaluation_split != "development"
            or item
            != {
                "candidate_id": request.candidate.candidate_id,
                "state": state,
                "questions": questions,
                "input_hash": fingerprint,
            }
            or digest(audience) != protocol["audience"]["hash"]
            or digest(rubric) != protocol["rubric_hash"]
        ):
            raise ValueError("Prepared request configuration or input lineage mismatch")
    store = service.store
    owner, lease = uid(), "diagnostic:" + protocol["protocol_hash"]
    if not store.acquire_lease(lease, owner):
        raise ValueError("This diagnostic protocol already has an active runner")
    heartbeat = asyncio.create_task(service._heartbeat(store, lease, owner))
    calls = 0
    try:
        persisted = store.get("diagnostic_freeze", protocol["protocol_hash"])
        if persisted:
            _validate_freeze(persisted, protocol, authorization)
            _write_once(directory / "judgment_freeze.json", persisted)
            return {"status": "already_frozen", "freeze_hash": persisted["freeze_hash"], "requests_made": 0}
        remaining = sum(
            b["per_attempt_usd"] * settings.max_attempts
            for b in protocol["estimate"]["per_request"]
            if not store.get("diagnostic_start", b["candidate_id"])
        )
        if store.spending()["charged_or_reserved_usd"] + remaining > authorization.pilot_ceiling_usd + 1e-12:
            raise ValueError("Separate pilot ceiling cannot reserve the remaining fixed cohort and retries")
        if (
            service.account_store.spending()["charged_or_reserved_usd"] + remaining
            > settings.spend_limit_usd + 1e-12
        ):
            raise ValueError(
                "Shared-account ceiling lacks headroom for the remaining fixed cohort and retries"
            )
        records = []
        for request, item in zip(requests, prepared, strict=True):
            candidate_id = request.candidate.candidate_id
            first = store.get("diagnostic_first", candidate_id)
            started = store.get("diagnostic_start", candidate_id)
            if not first and started:
                matches = [
                    j
                    for j in store.list("judgment")
                    if j["candidate_id"] == candidate_id and j["input_hash"] == item["input_hash"]
                ]
                if len(matches) != 1:
                    raise ValueError(
                        "Interrupted request has no unique persisted first result; reservation retained, manual review required; no resubmission"
                    )
                first = matches[0]
            if not first:
                await _wait_account_capacity(service)
                # Checkpoint before any call; interrupted unknown outcomes are never automatically resent.
                reservation = uid()
                store.put(
                    "diagnostic_start",
                    candidate_id,
                    {
                        "protocol_hash": protocol["protocol_hash"],
                        "reservation_id": reservation,
                        "started_at": now().isoformat(),
                        "input_hash": item["input_hash"],
                    },
                )
                budget = request_budget(item["state"], item["questions"], settings)
                store.reserve(
                    reservation,
                    budget["per_attempt_usd"] * settings.max_attempts,
                    authorization.pilot_ceiling_usd,
                    1000000,
                )
                result = await service.judge(request)
                calls += 1
                first = result.model_dump(mode="json")
            result = Judgment.model_validate(first)
            _validate_result(result, request, protocol, item["input_hash"])
            store.put("diagnostic_first", candidate_id, first)
            started = store.get("diagnostic_start", candidate_id)
            store.reconcile(started["reservation_id"], 0 if result.cached else result.estimated_cost)
            records.append({"candidate_id": candidate_id, "judgment": first})
            if store.spending()["charged_or_reserved_usd"] > authorization.pilot_ceiling_usd + 1e-12:
                break  # Unexpected provider usage over reservation: preserve first result; never keep spending.
        completed = {r["candidate_id"] for r in records}
        freeze = {
            "protocol_hash": protocol["protocol_hash"],
            "frozen_at": now().isoformat(),
            "execution_mode": "live",
            "records": records,
            "unexecuted_candidate_ids": [cid for cid in protocol["candidate_ids"] if cid not in completed],
            "authorization_hash": digest(authorization),
            "pilot_spending": store.spending(),
        }
        freeze["freeze_hash"] = digest(freeze)
        store.put("diagnostic_freeze", protocol["protocol_hash"], freeze)
        _write_once(directory / "judgment_freeze.json", freeze)
        return {
            "status": "frozen",
            "freeze_hash": freeze["freeze_hash"],
            "requests_made": calls,
            "record_count": len(records),
            "unexecuted_count": len(freeze["unexecuted_candidate_ids"]),
            "pilot_spending": freeze["pilot_spending"],
            "metrics_joined": False,
        }
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        store.release_lease(lease, owner)
