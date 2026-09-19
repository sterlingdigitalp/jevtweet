"""The sole versioned source of semantic questions and editorial policy."""

from __future__ import annotations

import json
import math
from copy import deepcopy
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _read_rubric() -> dict:
    rubric = json.loads((Path(__file__).parent / "config" / "rubric_v1.json").read_text())
    questions = rubric["questions"]
    for name, question in questions.items():
        if not question.get("instructions"):
            raise ValueError(f"Question {name} needs self-contained instructions")
        if question["type"] == "score":
            criteria = question["criteria"]
            if (
                not isinstance(criteria, list)
                or len(criteria) != 5
                or not all(isinstance(level, str) and level.strip() for level in criteria)
            ):
                raise ValueError(f"Question {name} must have five ordered descriptive criteria")
        elif question["type"] == "choice":
            if not isinstance(question["criteria"], dict) or len(question["criteria"]) < 2:
                raise ValueError(f"Question {name} needs named choice criteria")
        elif question["type"] == "noul":
            if set(question.get("criteria", {})) != {"true", "false"}:
                raise ValueError(f"Question {name} needs yes/no criteria")
        else:
            raise ValueError(f"Unsupported question type for {name}")
    for name, dimensions in rubric["profiles"].items():
        if "aversion" not in dimensions or len(dimensions) != len(set(dimensions)):
            raise ValueError(f"Invalid editorial profile {name}")
        if any(questions[key]["type"] != "score" for key in dimensions):
            raise ValueError(f"Profile {name} requires Score dimensions")
    policy = rubric["policy"]
    for key in ("aversion_penalty", "low_certainty", "missing_context_threshold", "instruction_threshold"):
        if not math.isfinite(policy[key]) or not 0 <= policy[key] <= 1:
            raise ValueError(f"Invalid policy threshold {key}")
    if type(policy["minimum_references"]) is not int or policy["minimum_references"] < 1:
        raise ValueError("A reference profile needs a positive minimum reference count")
    for key, weight in policy["positive_weights"].items():
        if key not in questions or not math.isfinite(weight) or weight <= 0:
            raise ValueError(f"Invalid positive weight {key}")
    for dimensions in rubric["profiles"].values():
        if any(key not in policy["positive_weights"] for key in dimensions if key != "aversion"):
            raise ValueError("Every positive dimension needs an explicit weight")
    for key in rubric["auxiliary_questions"] + rubric["required_checks"]:
        if key not in questions:
            raise ValueError(f"Unknown auxiliary question {key}")
    return rubric


def load_rubric() -> dict:
    """Return a fresh copy so request-specific code cannot mutate global policy."""
    return deepcopy(_read_rubric())


def reference_count(state: dict) -> int:
    """Count actual, nonempty supplied references, never a user-asserted count."""
    references = state.get("references", [])
    if not isinstance(references, list):
        return 0
    return len(
        {
            ref["text"].strip()
            for ref in references
            if isinstance(ref, dict) and isinstance(ref.get("text"), str) and ref["text"].strip()
        }
    )


def build_questions(state: dict, profile_id: str) -> dict:
    """Produce SDK-ready independent question specs, with no hidden state mutation.

    Reference eligibility is established by the allowlisted state builder; this
    additional count check avoids asking Jev to invent novelty without evidence.
    A missing distinctiveness answer remains missing in the requested profile.
    """
    rubric = load_rubric()
    if profile_id not in rubric["profiles"]:
        raise ValueError(f"Unknown editorial profile: {profile_id}")
    keys = (
        rubric["profiles"][profile_id] + rubric["auxiliary_questions"] + rubric["profile_checks"][profile_id]
    )
    return {
        key: {field: rubric["questions"][key][field] for field in ("type", "instructions", "criteria")}
        for key in keys
        if key != "distinctiveness" or reference_count(state) >= rubric["policy"]["minimum_references"]
    }
