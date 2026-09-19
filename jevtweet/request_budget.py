"""Shared tokenizer-independent sizing used by runtime and offline preparation."""

from .contracts import canonical


def request_budget(state, questions, settings):
    tokens = len(canonical({"state": state, "questions": questions}).encode()) + 1024
    longest = max((len(canonical(q).encode()) for q in questions.values()), default=0)
    pair = len(canonical(state).encode()) + longest + 512
    return {
        "sizing_rule": "canonical_utf8_bytes_plus_1024_v1",
        "reserved_input_tokens": tokens,
        "largest_state_question_tokens": pair,
        "within_limits": tokens <= settings.max_request_tokens and pair <= settings.max_state_question_tokens,
        "per_attempt_usd": tokens * settings.input_price_per_million / 1_000_000,
    }
