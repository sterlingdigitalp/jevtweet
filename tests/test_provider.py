"""Pinned SDK exercised through its mock HTTP transport; never real service calls."""

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx2
import pytest
import typesafe_sdk

from jevtweet.contracts import canonical
from jevtweet.provider import (
    API_BASE_URL,
    MockProvider,
    ProviderError,
    TypeSafeProvider,
    _retry_after,
    validate_response,
)
from jevtweet.rubric import build_questions
from jevtweet.settings import Settings


@pytest.fixture
def state():
    return {
        "candidate": {"text": "Synthetic example: a parser rejects malformed dates.", "media": {}},
        "audience": {"description": "Software builders"},
        "references": [],
        "evidence": {"missing": []},
    }


@pytest.fixture
def questions(state):
    return build_questions(state, "text_core_v1")


@pytest.fixture
def raw(questions):
    answers = {}
    for key, spec in questions.items():
        if spec["type"] == "score":
            answers[key] = {
                "type": "score",
                "score": 2.5,
                "confidence": 0.7,
                "probabilities": {"0": 0.0, "1": 0.0, "2": 0.5, "3": 0.5, "4": 0.0},
                "legend": {str(i): criterion for i, criterion in enumerate(spec["criteria"])},
            }
        elif spec["type"] == "choice":
            answers[key] = {
                "type": "choice",
                "choice": "assessable",
                "confidence": 0.8,
                "probabilities": {"assessable": 0.9, "not_assessable": 0.05, "unknown": 0.05},
            }
        else:
            answers[key] = {"type": "noul", "noul": 0.1}
    return {"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 321, "output_tokens": 45}}


def install_transport(monkeypatch, payload, *, status=200, headers=None):
    """The real SDK performs serialization/validation; only the network is replaced."""
    requests = []
    created = []
    client_class = typesafe_sdk.AsyncTypeSafeClient

    async def handler(request):
        requests.append(request)
        return httpx2.Response(status, json=payload, headers=headers or {})

    def client(**kwargs):
        created.append(kwargs)
        return client_class(**kwargs, transport=httpx2.MockTransport(handler))

    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(typesafe_sdk, "AsyncTypeSafeClient", client)
    return requests, created


@pytest.mark.asyncio
async def test_official_sdk_wire_contract_and_no_stacked_retries(monkeypatch, state, questions, raw):
    requests, clients = install_transport(monkeypatch, raw)
    monkeypatch.setenv("TYPESAFE_DEFAULT_MODEL", "jev-latest")
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://untrusted.invalid")
    provider = TypeSafeProvider(Settings(spend_limit_usd=1))
    result = await provider.ask(state, questions)
    assert result.error_category is None
    assert result.factors["relevance"].score == 2.5
    assert result.factors["missing_context"].confidence is None
    assert provider.execution_mode == "live"
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == f"{API_BASE_URL}/v1/systemone"
    body = json.loads(request.content)
    assert body == {"state": state, "model": "jev-1.13.0", "questions": questions}
    assert isinstance(body["questions"]["relevance"]["criteria"], list)
    assert clients[0]["retry"].max_retries == 0
    assert result.usage == {"input_tokens": 321, "output_tokens": 45}


@pytest.mark.parametrize("model", ["jev-latest", "jev-preview", "other", "jev-1.12.0", None, 13])
def test_model_identity_mismatch_quarantines_all_answers(raw, questions, model):
    raw["model"] = model
    result = validate_response(raw, questions, "jev-1.13.0")
    assert result.error_category == "model_mismatch"
    assert all(
        factor.score is None and factor.error == "model_mismatch" for factor in result.factors.values()
    )
    assert "raw_response" in result.diagnostics


@pytest.mark.parametrize("value", [-0.1, 4.1, float("nan"), float("inf"), "2.5", True, None])
def test_bad_score_cannot_replace_valid_neighbor(raw, questions, value):
    raw["answers"]["relevance"]["score"] = value
    result = validate_response(raw, questions, "jev-1.13.0")
    assert result.error_category == "invalid_response"
    assert result.factors["relevance"].score is None
    assert result.factors["clarity"].score == 2.5
    # Diagnostics for NaN and infinity remain storable with allow_nan=False.
    canonical(result)


@pytest.mark.parametrize(
    "change", ["missing", "extra", "bad_sum", "negative", "nonfinite", "score_mean", "legend", "wrong_type"]
)
def test_score_distribution_and_legend_validation(raw, questions, change):
    answer = raw["answers"]["relevance"]
    if change == "missing":
        answer["probabilities"].pop("4")
    elif change == "extra":
        answer["probabilities"]["5"] = 0
    elif change == "bad_sum":
        answer["probabilities"]["4"] = 0.2
    elif change == "negative":
        answer["probabilities"]["4"] = -0.01
    elif change == "nonfinite":
        answer["probabilities"]["4"] = float("nan")
    elif change == "score_mean":
        answer["score"] = 3.5
    elif change == "legend":
        answer["legend"]["0"] = "A different rubric"
    else:
        answer["type"] = "noul"
    result = validate_response(raw, questions, "jev-1.13.0")
    assert result.factors["relevance"].error
    assert result.factors["clarity"].score == 2.5


@pytest.mark.parametrize(
    "change",
    ["noul_confidence", "unknown_choice", "choice_not_maximum", "choice_support", "choice_confidence"],
)
def test_noul_and_choice_contracts(raw, questions, change):
    if change == "noul_confidence":
        key = "missing_context"
        raw["answers"][key]["confidence"] = 0.9
    else:
        key = "assessability"
        if change == "unknown_choice":
            raw["answers"][key]["choice"] = "surprise"
        elif change == "choice_not_maximum":
            raw["answers"][key]["choice"] = "unknown"
        elif change == "choice_support":
            raw["answers"][key]["probabilities"].pop("unknown")
        else:
            raw["answers"][key]["confidence"] = 1.1
    result = validate_response(raw, questions, "jev-1.13.0")
    assert result.factors[key].error
    assert result.factors["clarity"].score == 2.5


def test_missing_unknown_and_nonobject_answers_are_explicit(raw, questions):
    raw["answers"].pop("relevance")
    raw["answers"]["clarity"] = "not-an-object"
    raw["answers"]["unexpected_id"] = {"type": "noul", "noul": 0.5}
    result = validate_response(raw, questions, "jev-1.13.0")
    assert result.factors["relevance"].error == "missing_answer"
    assert result.factors["clarity"].error == "invalid_answer_object"
    assert result.diagnostics["unexpected_question_ids"] == ["unexpected_id"]
    assert "unexpected_id" not in result.factors
    assert result.factors["sharing"].score == 2.5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage", ["unknown_type", "missing_score", "coerced_string", "extra_noul_confidence"]
)
async def test_sdk_fault_salvage_uses_original_json(monkeypatch, state, questions, raw, damage):
    key = "relevance"
    if damage == "unknown_type":
        raw["answers"][key]["type"] = "future_primitive"
    elif damage == "missing_score":
        raw["answers"][key].pop("score")
    elif damage == "coerced_string":
        raw["answers"][key]["score"] = "2.5"
    else:
        key = "missing_context"
        raw["answers"][key]["confidence"] = 0.99
    requests, _ = install_transport(monkeypatch, raw)
    result = await TypeSafeProvider(Settings(spend_limit_usd=1)).ask(state, questions)
    assert result.factors[key].error
    assert result.factors["clarity"].score == 2.5
    assert len(requests) == 1
    assert result.error_category == "invalid_response"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,category,retryable",
    [
        (400, "request_schema", False),
        (401, "authentication", False),
        (402, "provider_credit", False),
        (403, "permission", False),
        (404, "model_or_endpoint_unavailable", False),
        (422, "request_schema", False),
        (429, "rate_limit", True),
        (500, "provider_server", True),
    ],
)
async def test_http_error_classification_no_internal_retries(
    monkeypatch, state, questions, status, category, retryable
):
    requests, _ = install_transport(
        monkeypatch,
        {"error": "Private provider detail must not escape"},
        status=status,
        headers={"Retry-After": "2"},
    )
    with pytest.raises(ProviderError) as error:
        await TypeSafeProvider(Settings(spend_limit_usd=1)).ask(state, questions)
    assert error.value.category == category
    assert error.value.retryable == retryable
    assert error.value.retry_after == 2
    assert "Private" not in str(error.value)
    assert len(requests) == 1


def test_retry_after_http_dates_and_bad_values():
    future = datetime.now(timezone.utc) + timedelta(seconds=60)
    delay = _retry_after({"retry-after": format_datetime(future)})
    assert 58 <= delay <= 60
    assert _retry_after({"retry-after": "3.5"}) == 3.5
    assert _retry_after({"retry-after": "-1"}) == 0
    for value in ("nan", "inf", "invalid-date", None):
        assert _retry_after({"retry-after": value}) is None


@pytest.mark.asyncio
async def test_credentials_and_spending_are_required_without_fallback(monkeypatch, state, questions):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="missing_credentials"):
        await TypeSafeProvider(Settings(spend_limit_usd=1)).ask(state, questions)
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-test-key")
    with pytest.raises(ProviderError, match="spend_limit_not_configured"):
        await TypeSafeProvider(Settings(spend_limit_usd=0)).ask(state, questions)
    with pytest.raises(ProviderError, match="unpinned_model"):
        TypeSafeProvider(Settings(model="jev-latest"))


def test_bad_usage_is_explicit_and_not_a_negative_credit(raw, questions):
    raw["usage"]["input_tokens"] = -200
    result = validate_response(raw, questions, "jev-1.13.0")
    assert result.usage["input_tokens"] is None
    assert result.error_category == "invalid_response"
    assert result.factors["relevance"].score == 2.5


def test_no_references_never_becomes_original(raw, state):
    populated = deepcopy(state)
    populated["references"] = [{"candidate_id": str(i), "text": f"Synthetic reference {i}"} for i in range(3)]
    questions = build_questions(populated, "reference_enriched_v1")
    raw["answers"]["distinctiveness"] = deepcopy(raw["answers"]["relevance"])
    raw["answers"]["distinctiveness"]["legend"] = {
        str(i): level for i, level in enumerate(questions["distinctiveness"]["criteria"])
    }
    result = validate_response(raw, questions, "jev-1.13.0", state)
    assert result.factors["distinctiveness"].assessability == "not_assessable"
    assert result.factors["distinctiveness"].error == "insufficient_references"
    assert result.factors["distinctiveness"].score == 2.5  # Preserve the unused raw answer.


@pytest.mark.asyncio
async def test_mock_does_not_call_real_sdk(monkeypatch, state, questions):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    def forbidden(**kwargs):
        raise AssertionError("Mock must never invoke the live SDK")

    monkeypatch.setattr(typesafe_sdk, "AsyncTypeSafeClient", forbidden)
    result = await MockProvider().ask(state, questions)
    assert result.model_returned == "synthetic_hash_mock_v1"


@pytest.mark.parametrize(
    "probabilities,value",
    [
        ([0, 0, 0, 1, 0], 2.98),
        ([0.02, 0.11, 0.32, 0.41, 0.13], 2.54),
        ([0.2, 0.2, 0.2, 0.2, 0.2], 2.02),
        ([0.2, 0.2, 0.2, 0.2, 0.21], 2.02),
        ([0.01, 0.25, 0.49, 0.25, 0.01], 2.0),
    ],
)
def test_jointly_feasible_rounded_wire_values_preserved(raw, questions, probabilities, value):
    """Invented probability vectors exercise wire precision, not model performance."""
    answer = raw["answers"]["relevance"]
    answer["probabilities"] = {str(index): probability for index, probability in enumerate(probabilities)}
    answer["score"] = value
    original = deepcopy(raw)
    result = validate_response(raw, questions, "jev-1.13.0")
    assert result.error_category is None
    factor = result.factors["relevance"]
    assert factor.error is None
    assert factor.score == value
    assert factor.probabilities == answer["probabilities"]
    assert raw == original
    note = result.diagnostics["numerical_precision_notes"]["relevance"]
    assert note["validation_version"] == "response_validation_v2"
    assert note["reason"] == "feasible_two_decimal_rounding"
    assert "raw values retained unchanged" in note["review_note"]


@pytest.mark.parametrize(
    "probabilities,value",
    [
        ([0, 0, 0, 1, 0], 2.97),  # Inside a crude .055 allowance, outside the feasible mean interval.
        ([0.2, 0.2, 0.2, 0.2, 0.2], 2.04),
        ([0.2, 0.2, 0.2, 0.2, 0.2], 1.96),
        ([0.21, 0.21, 0.21, 0.21, 0.19], 2.0),  # Sum 1.03 cannot round from any probability vector.
        ([0.2, 0.2, 0.2, 0.2, 0.17], 2.0),
        ([0, 0, 0, 0, 0.98], 3.99),  # Sum is within .025, but bounds at zero make it infeasible.
    ],
)
def test_rounded_values_outside_joint_feasibility_remain_unavailable(raw, questions, probabilities, value):
    answer = raw["answers"]["relevance"]
    answer["probabilities"] = {str(index): probability for index, probability in enumerate(probabilities)}
    answer["score"] = value
    result = validate_response(raw, questions, "jev-1.13.0")
    assert result.error_category == "invalid_response"
    assert result.factors["relevance"].score is None
    assert result.factors["relevance"].error
    assert result.factors["clarity"].score == 2.5
    assert "relevance" not in result.diagnostics.get("numerical_precision_notes", {})


@pytest.mark.parametrize(
    "probabilities,value",
    [
        ([0.2, 0.2, 0.2, 0.2, 0.2], 2.019),
        ([0.2001, 0.2, 0.2, 0.2, 0.1999], 2.02),
        ([0.201, 0.201, 0.2, 0.2, 0.201], 2.0),
    ],
)
def test_extra_precision_never_gets_rounded_response_allowance(raw, questions, probabilities, value):
    answer = raw["answers"]["relevance"]
    answer["probabilities"] = {str(index): probability for index, probability in enumerate(probabilities)}
    answer["score"] = value
    result = validate_response(raw, questions, "jev-1.13.0")
    assert result.error_category == "invalid_response"
    assert result.factors["relevance"].score is None
    assert "numerical_precision_notes" not in result.diagnostics


def test_exact_distribution_gets_no_rounding_notice(raw, questions):
    result = validate_response(raw, questions, "jev-1.13.0")
    assert result.error_category is None
    assert "numerical_precision_notes" not in result.diagnostics


@pytest.mark.asyncio
async def test_sdk_rounded_answer_remains_success_with_private_precision_note(
    monkeypatch, state, questions, raw
):
    raw["answers"]["relevance"]["probabilities"] = {str(index): 0.2 for index in range(5)}
    raw["answers"]["relevance"]["score"] = 2.02
    requests, _ = install_transport(monkeypatch, raw)
    result = await TypeSafeProvider(Settings(spend_limit_usd=1)).ask(state, questions)
    assert result.error_category is None
    assert result.factors["relevance"].score == 2.02
    assert (
        result.diagnostics["numerical_precision_notes"]["relevance"]["reason"]
        == "feasible_two_decimal_rounding"
    )
    assert len(requests) == 1
