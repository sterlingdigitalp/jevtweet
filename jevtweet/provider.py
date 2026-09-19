"""Official SDK adapter and explicit deterministic synthetic adapter.

The runtime owns request admission, account-wide limiting, spending reservations,
timeouts, and capped retries. This module performs exactly one SDK attempt and
never falls back to another model or execution mode.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from importlib.metadata import version
from typing import Any

from .contracts import Factor, ProviderResult, canonical
from .rubric import load_rubric, reference_count
from .settings import Settings

SDK_VERSION = "0.7.0"
API_BASE_URL = "https://api.typesafe.ai"


class ProviderError(Exception):
    """Sanitized operational error; raw SDK messages may contain private payloads."""

    def __init__(self, category: str, retryable: bool = False, retry_after: float | None = None):
        super().__init__(category)
        self.category = category
        self.retryable = retryable
        self.retry_after = retry_after


def _number(value: Any, *, maximum: float = 1) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
        raise ValueError("nonfinite_or_out_of_range_number")
    return float(value)


def _distribution(
    value: Any, support: set[str], tolerance: float, *, check_sum: bool = True
) -> dict[str, float]:
    if not isinstance(value, dict) or set(value) != support:
        raise ValueError("invalid_distribution_support")
    probabilities = {key: _number(probability) for key, probability in value.items()}
    if check_sum and not math.isclose(math.fsum(probabilities.values()), 1, rel_tol=0, abs_tol=tolerance):
        raise ValueError("invalid_distribution_sum")
    return probabilities


def _score_precision(score: float, probabilities: dict[str, float], validation: dict) -> dict | None:
    """Check exact values first, then joint feasibility of rounded wire values.

    Some service responses expose scores and probabilities rounded separately to
    two decimals. Their displayed mean need not equal their displayed score.
    For quantized responses only, seek a probability vector inside the displayed
    rounding intervals, summing to one, whose mean can round to the shown score.
    A box intersected with the probability simplex is convex, so the achievable
    mean is the entire interval between these greedily computed extrema.
    Original scores and distributions are never replaced or normalized.
    """
    displayed_sum = math.fsum(probabilities.values())
    displayed_mean = math.fsum(int(level) * probability for level, probability in probabilities.items())
    valid_sum = math.isclose(
        displayed_sum, 1, rel_tol=0, abs_tol=validation["probability_sum_absolute_tolerance"]
    )
    valid_mean = math.isclose(
        score, displayed_mean, rel_tol=0, abs_tol=validation["expected_score_absolute_tolerance"]
    )
    if valid_sum and valid_mean:
        return None
    rejection = "invalid_distribution_sum" if not valid_sum else "score_distribution_mismatch"
    scale = 10 ** validation["rounded_decimal_places"]
    if not all(
        math.isclose(value * scale, round(value * scale), rel_tol=0,
                     abs_tol=validation["quantization_absolute_tolerance"])
        for value in [score, *probabilities.values()]
    ):
        raise ValueError(rejection)
    half_unit = .5 / scale
    slack = validation["interval_feasibility_absolute_tolerance"]
    levels = sorted(int(level) for level in probabilities)
    lower = {level: max(0., probabilities[str(level)] - half_unit) for level in levels}
    upper = {level: min(1., probabilities[str(level)] + half_unit) for level in levels}
    lower_sum = math.fsum(lower.values())
    if lower_sum > 1 + slack or math.fsum(upper.values()) < 1 - slack:
        raise ValueError("invalid_distribution_sum")

    def extreme(order: list[int]) -> float:
        remaining = max(0., 1 - lower_sum)
        weights = lower.copy()
        for level in order:
            addition = min(remaining, upper[level] - lower[level])
            weights[level] += addition
            remaining -= addition
        return math.fsum(level * weight for level, weight in weights.items())

    minimum_mean, maximum_mean = extreme(levels), extreme(list(reversed(levels)))
    score_low, score_high = max(0., score - half_unit), min(float(max(levels)), score + half_unit)
    if score_high < minimum_mean - slack or score_low > maximum_mean + slack:
        raise ValueError("score_distribution_mismatch")
    return {
        "validation_version": validation["version"],
        "reason": "feasible_two_decimal_rounding",
        "review_note": "Separately rounded wire values are jointly feasible; raw values retained unchanged.",
        "displayed_probability_sum": displayed_sum,
        "displayed_probability_mean": displayed_mean,
        "feasible_mean_interval": [minimum_mean, maximum_mean],
        "reported_score_interval": [score_low, score_high],
    }


def _safe_diagnostic(value: Any) -> Any:
    """Preserve malformed private responses without breaking JSON persistence."""
    if isinstance(value, float) and not math.isfinite(value):
        return {"invalid_nonfinite_number": str(value)}
    if isinstance(value, dict):
        return {str(key): _safe_diagnostic(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_diagnostic(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return {"unexpected_python_type": type(value).__name__}


def _unavailable(question_id: str, spec: dict, error: str) -> Factor:
    return Factor(question_id=question_id, type=spec["type"], assessability="unknown", error=error)


def _evidence_paths(state: dict, question_id: str) -> list[str]:
    paths = ["candidate.text", "audience"]
    candidate = state.get("candidate", {})
    for field in ("parent", "quoted"):
        if candidate.get(field):
            paths.append(f"candidate.{field}")
    if candidate.get("media", {}).get("description"):
        paths.append("candidate.media.description (text description only)")
    if state.get("topic"):
        paths.append("topic")
    if question_id == "distinctiveness":
        paths.extend(f"reference:{ref['candidate_id']}" for ref in state.get("references", []) if ref.get("candidate_id"))
    return paths


def validate_response(raw: Any, questions: dict, model: str, state: dict | None = None) -> ProviderResult:
    """Validate original wire fields, retaining every independently valid answer.

    SDK 0.7.0 deliberately ignores unknown answer types and Pydantic may coerce
    scalar values. Inspecting original JSON here prevents silent normalization
    from converting a malformed answer into an apparently valid factor.
    """
    rubric = load_rubric()
    validation = rubric["validation"]
    tolerance = validation["probability_sum_absolute_tolerance"]
    diagnostics: dict[str, Any] = {}
    precision_notes: dict[str, dict] = {}
    factors: dict[str, Factor] = {}
    returned = raw.get("model") if isinstance(raw, dict) else None
    usage: dict[str, int | None] = {}
    if isinstance(raw, dict) and isinstance(raw.get("usage"), dict):
        for key in ("input_tokens", "output_tokens"):
            value = raw["usage"].get(key)
            if value is None or type(value) is int and value >= 0:
                usage[key] = value
            else:
                diagnostics.setdefault("invalid_usage", []).append(key)
                usage[key] = None
    else:
        diagnostics["invalid_usage"] = "missing_usage_object"
    if returned != model:
        return ProviderResult(
            factors={key: _unavailable(key, spec, "model_mismatch") for key, spec in questions.items()},
            model_returned=returned if isinstance(returned, str) else None,
            usage=usage,
            error_category="model_mismatch",
            diagnostics={"expected_model": model, "raw_response": _safe_diagnostic(raw)},
        )
    answers = raw.get("answers")
    if not isinstance(answers, dict):
        answers = {}
        diagnostics["answers"] = "missing_or_invalid_answers_object"
    unexpected = sorted(set(answers) - set(questions))
    if unexpected:
        diagnostics["unexpected_question_ids"] = unexpected
    for key, spec in questions.items():
        answer = answers.get(key)
        try:
            if not isinstance(answer, dict):
                raise ValueError("missing_answer" if key not in answers else "invalid_answer_object")
            if answer.get("type") != spec["type"]:
                raise ValueError("answer_type_mismatch")
            factor_args: dict[str, Any] = {"question_id": key, "type": spec["type"]}
            if spec["type"] == "score":
                if set(answer) != {"type", "score", "confidence", "probabilities", "legend"}:
                    raise ValueError("invalid_score_fields")
                support = {str(index) for index in range(len(spec["criteria"]))}
                probabilities = _distribution(answer["probabilities"], support, tolerance, check_sum=False)
                legend = {str(index): criterion for index, criterion in enumerate(spec["criteria"])}
                if answer["legend"] != legend:
                    raise ValueError("legend_mismatch")
                value = _number(answer["score"], maximum=len(spec["criteria"]) - 1)
                # The v1 Factor contract deliberately supports five-level Scores.
                if len(spec["criteria"]) != 5 or not all(isinstance(item, str) for item in spec["criteria"]):
                    raise ValueError("unsupported_score_rubric")
                factor_args.update(score=value, confidence=_number(answer["confidence"]),
                                   probabilities=probabilities, legend=legend)
                precision_note = _score_precision(value, probabilities, validation)
                if precision_note is not None:
                    precision_notes[key] = precision_note
            elif spec["type"] == "choice":
                if set(answer) != {"type", "choice", "confidence", "probabilities"}:
                    raise ValueError("invalid_choice_fields")
                probabilities = _distribution(answer["probabilities"], set(spec["criteria"]), tolerance)
                choice = answer["choice"]
                if not isinstance(choice, str) or choice not in probabilities:
                    raise ValueError("unknown_choice")
                if probabilities[choice] + tolerance < max(probabilities.values()):
                    raise ValueError("choice_distribution_mismatch")
                factor_args.update(choice=choice, confidence=_number(answer["confidence"]), probabilities=probabilities)
            else:
                if set(answer) != {"type", "noul"}:
                    raise ValueError("invalid_noul_fields")
                factor_args["noul"] = _number(answer["noul"])
            if state is not None:
                factor_args["evidence_references"] = _evidence_paths(state, key)
                if key == "distinctiveness" and reference_count(state) < rubric["policy"]["minimum_references"]:
                    factor_args["assessability"] = "not_assessable"
                    factor_args["error"] = "insufficient_references"
            factors[key] = Factor(**factor_args)
        except (ValueError, TypeError, KeyError) as exc:
            reason = str(exc) if isinstance(exc, ValueError) and type(exc) is ValueError else "invalid_answer_schema"
            factors[key] = _unavailable(key, spec, reason)
            diagnostics.setdefault("invalid_answers", {})[key] = reason
    has_errors = bool(diagnostics)
    if has_errors:
        diagnostics["raw_response"] = _safe_diagnostic(raw)
    if precision_notes:
        diagnostics["numerical_precision_notes"] = precision_notes
    return ProviderResult(
        factors=factors,
        model_returned=returned,
        usage=usage,
        error_category="invalid_response" if has_errors else None,
        diagnostics=diagnostics,
    )


def _retry_after(headers: Any) -> float | None:
    value = headers.get("retry-after") if headers is not None else None
    if value is None:
        return None
    try:
        delay = float(value)
    except (ValueError, TypeError):
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            delay = (parsed - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0, delay) if math.isfinite(delay) else None


class TypeSafeProvider:
    name = "typesafe_sdk"
    execution_mode = "live"
    sdk_version = SDK_VERSION

    def __init__(self, settings: Settings):
        self.settings = settings
        if not re.fullmatch(r"jev-\d+\.\d+\.\d+", settings.model):
            raise ProviderError("unpinned_model")

    async def ask(self, state: dict, questions: dict) -> ProviderResult:
        """Make one real SDK call. The caller must reserve spending first."""
        if not os.getenv("TYPESAFE_API_KEY", "").strip():
            raise ProviderError("missing_credentials")
        if self.settings.spend_limit_usd <= 0:
            raise ProviderError("spend_limit_not_configured")
        if version("typesafe-sdk") != SDK_VERSION:
            raise ProviderError("sdk_version_mismatch")
        from typesafe_sdk import (
            AsyncTypeSafeClient, Choice, Noul, RetryPolicy, Score,
            TypeSafeAPIConnectionError, TypeSafeAPIError,
            TypeSafeAPIResponseValidationError, TypeSafeAPITimeoutError, TypeSafeError,
        )
        try:
            constructors = {"score": Score, "choice": Choice, "noul": Noul}
            typed_questions = {
                key: constructors[spec["type"]](**spec) for key, spec in questions.items()
            }
            if not typed_questions:
                raise ProviderError("empty_questions")
            async with AsyncTypeSafeClient(
                model=self.settings.model,
                retry=RetryPolicy(max_retries=0),
                timeout=self.settings.timeout_seconds,
                # Do not let an unrelated SDK environment override send private
                # content or credentials to another service.
                base_url=API_BASE_URL,
            ) as client:
                response = await client.system_one(state=state, questions=typed_questions, model=self.settings.model)
                raw = response.raw_http_response.json()
        except TypeSafeAPIResponseValidationError as exc:
            raw = exc.body
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except ValueError:
                    pass
            result = validate_response(raw, questions, self.settings.model, state)
            result.diagnostics["sdk_validation_field"] = exc.field_path
            result.error_category = result.error_category or "invalid_response"
            return result
        except TypeSafeAPITimeoutError:
            raise ProviderError("timeout", retryable=True) from None
        except TypeSafeAPIConnectionError:
            raise ProviderError("connection", retryable=True) from None
        except TypeSafeAPIError as exc:
            status = exc.status
            category = {
                400: "request_schema", 401: "authentication", 402: "provider_credit",
                403: "permission", 404: "model_or_endpoint_unavailable", 422: "request_schema",
                429: "rate_limit",
            }.get(status, "provider_server" if status >= 500 else "provider_http")
            raise ProviderError(category, retryable=status in {408, 429} or 500 <= status <= 599,
                                retry_after=_retry_after(exc.headers)) from None
        except TypeSafeError:
            raise ProviderError("sdk_request") from None
        except (ValueError, TypeError, KeyError):
            raise ProviderError("request_or_response_schema") from None
        return validate_response(raw, questions, self.settings.model, state)


class MockProvider:
    """Hash-generated synthetic wiring fixtures; no semantic or predictive claims."""

    name = "synthetic_hash_mock_v1"
    execution_mode = "mock"
    sdk_version = SDK_VERSION

    async def ask(self, state: dict, questions: dict) -> ProviderResult:
        candidate = state.get("candidate", {})
        empty = not str(candidate.get("text", "")).strip() and not candidate.get("media", {}).get("description")
        missing = state.get("evidence", {}).get("missing", [])
        essential = any("essential" in item for item in missing)
        answers = {}
        for key, spec in questions.items():
            seed = hashlib.sha256(canonical({"state": state, "question": spec}).encode()).digest()
            if spec["type"] == "score":
                value = 0 if empty else seed[0] % 5
                answers[key] = {
                    "type": "score", "score": value, "confidence": 1.0,
                    "probabilities": {str(index): float(index == value) for index in range(5)},
                    "legend": {str(index): criterion for index, criterion in enumerate(spec["criteria"])},
                }
            elif spec["type"] == "choice":
                choice = "not_assessable" if empty or essential else "assessable"
                if key != "assessability":
                    choice = list(spec["criteria"])[seed[0] % len(spec["criteria"])]
                answers[key] = {"type": "choice", "choice": choice, "confidence": 1.0,
                                "probabilities": {option: float(option == choice) for option in spec["criteria"]}}
            else:
                # These fixed guard answers are fixture mechanics, not a text
                # injection detector or evidence-completeness model.
                value = float(essential) if key == "missing_context" else 0.0
                if key not in {"missing_context", "instruction_like"}:
                    value = seed[0] / 255
                answers[key] = {"type": "noul", "noul": value}
        raw = {"model": self.name, "answers": answers, "usage": {"input_tokens": 0, "output_tokens": 0}}
        result = validate_response(raw, questions, self.name, state)
        result.diagnostics["synthetic_notice"] = "Deterministic hash fixtures; not Jev judgments or predictive evidence."
        return result
