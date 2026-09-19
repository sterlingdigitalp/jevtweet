"""Software-policy checks using original synthetic answers, never service benchmarks."""

import math
from copy import deepcopy

import pytest

from jevtweet.contracts import Factor
from jevtweet.provider import MockProvider
from jevtweet.rubric import build_questions, load_rubric
from jevtweet.scoring import score


def complete(raw=2.0, risk=0.0, confidence=0.9):
    rubric = load_rubric()
    result = {
        key: Factor(
            question_id=key, type="score", score=risk if key == "aversion" else raw, confidence=confidence
        )
        for key in rubric["profiles"]["reference_enriched_v1"] + ["direct_overall"]
    }
    result.update(
        reference_adequacy=Factor(
            question_id="reference_adequacy", type="choice", choice="adequate", confidence=1
        ),
        assessability=Factor(question_id="assessability", type="choice", choice="assessable", confidence=1),
        missing_context=Factor(question_id="missing_context", type="noul", noul=0),
        instruction_like=Factor(question_id="instruction_like", type="noul", noul=0),
    )
    return result


@pytest.mark.parametrize(
    "raw,continuous,integer",
    [(0, 1, 1), (0.5, 1.5, 2), (1.5, 2.5, 3), (2.5, 3.5, 4), (3.5, 4.5, 5), (4, 5, 5)],
)
def test_zero_based_conversion_and_half_up_rounding(raw, continuous, integer):
    result = score(complete(raw), "text_core_v1")
    assert result["status"] == "scored"
    assert result["score_continuous"] == pytest.approx(continuous)
    assert result["score_1_to_5"] == integer


@pytest.mark.parametrize("profile", ["text_core_v1", "reference_enriched_v1"])
def test_risk_monotonicity_and_finite_bounds(profile):
    for raw in [0, 0.01, 0.5, 1, 1.7, 2.3, 3, 3.7, 4]:
        previous = 5
        for risk in [0, 0.1, 1, 2, 3.5, 4]:
            result = score(complete(raw, risk), profile)
            assert 1 <= result["score_continuous"] <= previous
            assert 1 <= result["score_1_to_5"] <= 5
            assert math.isfinite(result["score_continuous"])
            previous = result["score_continuous"]


def test_explanation_contributions_reconstruct_calculation():
    factors = complete(3, 2)
    factors["clarity"].score = 1
    result = score(factors, "text_core_v1")
    assert result["quality"] == pytest.approx(2 / 3)
    assert result["risk_penalty"] == 0.125
    assert result["score_continuous"] == pytest.approx(1 + 4 * (2 / 3 - 0.125))
    assert (
        math.fsum(item["quality_contribution"] for item in result["explanation"]["contributions"])
        == result["quality"]
    )
    assert result["explanation"]["weakest_factors"][0]["question_id"] == "clarity"
    assert result["explanation"]["risk_penalty_rating_points"] == 0.5
    assert "not a calibrated probability" in result["explanation"]["meaning"]


def test_no_aversion_does_not_award_positive_content_points():
    result = score(complete(0, 0), "text_core_v1")
    assert result["quality"] == 0
    assert result["score_continuous"] == 1


def test_certainty_is_a_review_signal_not_score_multiplier():
    high = score(complete(3, confidence=1), "text_core_v1")
    low = score(complete(3, confidence=0.01), "text_core_v1")
    assert high["score_continuous"] == low["score_continuous"]
    assert low["score_1_to_5"] == high["score_1_to_5"]
    assert "low_answer_certainty:relevance" in low["review_flags"]
    assert high["review_flags"] == []


@pytest.mark.parametrize(
    "missing", ["relevance", "clarity", "sharing", "conversation", "attention", "follow", "aversion"]
)
def test_required_factor_missing_does_not_renormalize(missing):
    factors = complete(4)
    del factors[missing]
    result = score(factors, "text_core_v1")
    assert result["status"] == "partial"
    assert result["score_continuous"] is result["score_1_to_5"] is None
    assert f"missing_factor:{missing}" in result["review_flags"]
    if missing != "aversion":
        assert result["quality"] is None


def test_unavailable_novelty_requires_a_separate_explicit_core_judgment():
    factors = complete(4)
    factors["distinctiveness"].assessability = "not_assessable"
    enriched = score(factors, "reference_enriched_v1")
    assert enriched["status"] == "partial"
    assert enriched["score_continuous"] is None
    assert score(factors, "text_core_v1")["score_1_to_5"] == 5


@pytest.mark.parametrize(
    "evidence",
    ["essential_parent_context", "essential_quoted_context", "essential_media_description", "empty_content"],
)
def test_missing_essential_evidence_blocks_a_high_headline(evidence):
    result = score(complete(4), "text_core_v1", missing_evidence=[evidence])
    assert result["status"] in {"partial", "abstained"}
    assert result["score_continuous"] is result["score_1_to_5"] is None
    assert evidence in result["explanation"]["missing_evidence"]


def test_unavailable_future_author_history_is_disclosed_not_quality_penalized():
    result = score(complete(4), "text_core_v1", missing_evidence=["future_author_metadata_excluded"])
    assert result["status"] == "scored"
    assert result["score_1_to_5"] == 5
    assert "missing_evidence:future_author_metadata_excluded" in result["review_flags"]


@pytest.mark.parametrize("key", ["assessability", "missing_context"])
def test_missing_required_check_blocks_scoring(key):
    factors = complete(4)
    del factors[key]
    result = score(factors, "text_core_v1")
    assert result["status"] == "partial"
    assert result["score_1_to_5"] is None


@pytest.mark.parametrize("choice,status", [("unknown", "partial"), ("not_assessable", "abstained")])
def test_model_assessability_states(choice, status):
    factors = complete(4)
    factors["assessability"].choice = choice
    result = score(factors, "text_core_v1")
    assert result["status"] == status
    assert result["score_1_to_5"] is None


def test_context_and_injection_thresholds_are_separate():
    factors = complete(4)
    factors["instruction_like"].noul = 0.99
    result = score(factors, "text_core_v1")
    assert result["status"] == "scored"
    assert "instruction_like_content" in result["review_flags"]
    factors["missing_context"].noul = load_rubric()["policy"]["missing_context_threshold"]
    result = score(factors, "text_core_v1")
    assert result["status"] == "partial"
    assert result["score_1_to_5"] is None


def test_direct_baseline_never_enters_composite():
    factors = complete(1)
    factors["direct_overall"].score = 4
    assert score(factors, "text_core_v1")["score_1_to_5"] == 2
    factors.pop("direct_overall")
    assert score(factors, "text_core_v1")["score_1_to_5"] == 2


def test_operational_failure_never_becomes_one():
    result = score({}, "text_core_v1")
    assert result["status"] == "failed"
    assert result["score_1_to_5"] is result["score_continuous"] is None
    assert result["quality"] is result["risk_penalty"] is None


@pytest.mark.parametrize("raw", [-1, 5, float("nan"), float("inf")])
def test_defense_against_unvalidated_mutated_record(raw):
    factors = complete()
    factors["relevance"] = factors["relevance"].model_copy(update={"score": raw})
    result = score(factors, "text_core_v1")
    assert result["status"] == "partial"
    assert result["score_continuous"] is None


def test_scoring_never_mutates_answers_or_config():
    factors = complete()
    original = deepcopy(factors)
    rubric = load_rubric()
    rubric["policy"]["aversion_penalty"] = 1
    score(factors, "reference_enriched_v1")
    assert factors == original
    assert load_rubric()["policy"]["aversion_penalty"] == 0.25


def test_questions_are_complete_ordered_specs_and_reference_aware():
    state = {"candidate": {"text": "Synthetic original candidate"}, "references": [], "audience": {}}
    core = build_questions(state, "text_core_v1")
    enriched_missing = build_questions(state, "reference_enriched_v1")
    assert "distinctiveness" not in enriched_missing
    assert core == {k: v for k, v in enriched_missing.items() if k != "reference_adequacy"}
    assert enriched_missing["reference_adequacy"]["type"] == "choice"
    state["references"] = [{"candidate_id": str(i), "text": f"Synthetic reference {i}"} for i in range(3)]
    assert "distinctiveness" in build_questions(state, "reference_enriched_v1")
    for spec in core.values():
        assert set(spec) == {"type", "instructions", "criteria"}
        assert "candidate" in spec["instructions"]
        if spec["type"] == "score":
            assert isinstance(spec["criteria"], list) and len(spec["criteria"]) == 5
    state["references"] = [state["references"][0]] * 3
    state["evidence"] = {"reference_count": 100}
    assert "distinctiveness" not in build_questions(state, "reference_enriched_v1")


def test_unknown_profile_is_rejected():
    with pytest.raises(ValueError):
        score(complete(), "unversioned")
    with pytest.raises(ValueError):
        build_questions({}, "unversioned")


@pytest.mark.asyncio
async def test_benign_empty_mock_abstains_and_fixture_mode_is_explicit():
    state = {"candidate": {"text": "   ", "media": {}}, "evidence": {"missing": ["empty_content"]}}
    questions = build_questions(state, "text_core_v1")
    provider = MockProvider()
    response = await provider.ask(state, questions)
    result = score(response.factors, "text_core_v1", missing_evidence=["empty_content"])
    assert result["status"] == "abstained"
    assert result["score_1_to_5"] is None
    assert provider.execution_mode == "mock"
    assert response.model_returned == "synthetic_hash_mock_v1"
    assert response.usage["input_tokens"] == 0
    assert "not Jev judgments" in response.diagnostics["synthetic_notice"]


@pytest.mark.asyncio
async def test_synthetic_adapter_is_deterministic():
    state = {"candidate": {"text": "Synthetic launch notes: a tiny parser with a concrete worked example."}}
    questions = build_questions(state, "text_core_v1")
    first = await MockProvider().ask(state, questions)
    second = await MockProvider().ask(state, questions)
    assert first == second
    assert score(first.factors, "text_core_v1")["status"] == "scored"


def test_irrelevant_reference_set_never_becomes_novelty():
    factors = complete()
    factors["reference_adequacy"].choice = "not_assessable"
    result = score(factors, "reference_enriched_v1")
    assert result["status"] == "partial" and result["score_1_to_5"] is None
    assert result["factors"]["distinctiveness"].assessability == "not_assessable"
    assert factors["distinctiveness"].assessability == "assessable"
