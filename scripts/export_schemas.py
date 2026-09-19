"""Generate reviewable JSON schemas from the authoritative Pydantic contracts."""

import json
from pathlib import Path

from jevtweet import contracts

classes = [
    "Candidate",
    "Audience",
    "PredictionContext",
    "OutcomeObservation",
    "Judgment",
    "JudgeRequest",
    "Annotation",
    "ImportRequest",
    "JobRequest",
    "EvaluationRequest",
    "DiscoveryRequest",
]
target = Path(__file__).resolve().parent.parent / "schemas"
target.mkdir(exist_ok=True)
for name in classes:
    (target / f"{name}.v1.json").write_text(
        json.dumps(getattr(contracts, name).model_json_schema(), indent=2) + "\n"
    )
print(f"Generated {len(classes)} v1 schemas from authoritative contracts.")
