from pathlib import Path

from .contracts import Candidate, JudgeRequest, OutcomeObservation, PredictionContext
from .corpus import attach_outcome, export_results, import_candidates


async def demonstrate(service):
    """Deterministic synthetic mechanics only, never a predictive benchmark."""
    fixtures = Path(__file__).parent.parent / "fixtures"
    report = import_candidates(service.store, (fixtures / "candidates_synthetic.jsonl").read_text())
    ids = [f"{r['candidate_id']}:{r['candidate_version']}" for r in report["preview"]]
    if not ids:
        ids = [
            f"{r['candidate_id']}:{r['candidate_version']}"
            for r in service.store.list("candidate")
            if r["synthetic"]
        ]
    job = service.create_job(ids)
    completed = await service.run_job(job["job_id"])
    resumed = await service.run_job(job["job_id"])
    for row in (fixtures / "outcomes_synthetic.jsonl").read_text().splitlines():
        attach_outcome(service.store, OutcomeObservation.model_validate_json(row))
    a = Candidate(
        candidate_id="synthetic-compare-a",
        text="A generated migration dropped our index. We added a query-plan regression check.",
        content_available_at="2026-01-20T00:00:00Z",
        synthetic=True,
    )
    b = a.model_copy(
        update={
            "candidate_id": "synthetic-compare-b",
            "text": "Our new pre-merge check replays generated migrations and compares query plans. It caught a dropped index this morning.",
        }
    )
    context = PredictionContext(prediction_cutoff="2026-01-20T00:00:00Z")
    comparison = await service.compare([JudgeRequest(candidate=x, context=context) for x in (a, b)])
    from .evaluation import evaluate

    experiment = evaluate(service.store, synthetic=True)
    from .rubric import load_rubric

    export = export_results(
        service.store,
        "csv",
        execution_mode="mock",
        profile_id="text_core_v1",
        audience_id="production_ai_coding",
        rubric_version=load_rubric()["version"],
    )
    output = service.settings.data_dir / "demo_export.csv"
    output.write_text(export)
    return {
        "execution_mode": "mock",
        "synthetic": True,
        "predictive_validation": "not_established",
        "import": report,
        "job": completed,
        "resume_status": resumed["status"],
        "comparison": comparison,
        "experiment": experiment,
        "export": str(output),
        "spending": service.account_store.spending(),
    }
