import argparse
import asyncio
import json
import sys
from pathlib import Path

from .contracts import Candidate, JudgeRequest, OutcomeObservation
from .corpus import attach_outcome, export_results, import_candidates, read_rows
from .service import Service
from .settings import Settings


def parser():
    p = argparse.ArgumentParser(
        prog="jevtweet", description="Local Jev editorial judge and evidence-gated evaluation lab"
    )
    p.add_argument("--data-dir", type=Path, default=None)
    sub = p.add_subparsers(dest="command", required=True)
    j = sub.add_parser("judge")
    j.add_argument("--text")
    j.add_argument("--input", type=Path)
    j.add_argument("--live", action="store_true")
    j.add_argument("--audience", default="production_ai_coding")
    j.add_argument("--profile", default="text_core_v1")
    j.add_argument("--output", type=Path)
    j.add_argument("--references", action="store_true")
    i = sub.add_parser("import")
    i.add_argument("path", type=Path)
    i.add_argument("--format", choices=["csv", "jsonl"])
    i.add_argument("--mapping", default="{}")
    i.add_argument("--preview", action="store_true")
    b = sub.add_parser("batch")
    b.add_argument("--ids", nargs="*")
    b.add_argument("--audience", default="production_ai_coding")
    b.add_argument("--profile", default="text_core_v1")
    b.add_argument("--live", action="store_true")
    for name in ["resume", "cancel", "inspect"]:
        s = sub.add_parser(name)
        s.add_argument("id")
    o = sub.add_parser("attach-outcomes")
    o.add_argument("path", type=Path)
    o.add_argument("--format", choices=["csv", "jsonl"])
    c = sub.add_parser("compare")
    c.add_argument("path", type=Path)
    for name in ("evaluate", "preflight", "freeze-evaluation", "readiness"):
        e = sub.add_parser(name)
        e.add_argument("--synthetic", action="store_true")
        e.add_argument("--task", default="breakout_48h_v1")
        e.add_argument("--representative-sampling", action="store_true")
        e.add_argument("--population", default="")
        e.add_argument("--sampling-declaration", default="")
        e.add_argument("--max-rows", type=int, default=5000)
        e.add_argument("--output", type=Path)
        e.add_argument("--cohort", default="{}")
    opening = sub.add_parser("open-holdout")
    opening.add_argument("experiment_id")
    opening.add_argument("--frozen-candidate-hash", required=True)
    opening.add_argument("--output", type=Path)
    ec = sub.add_parser("compare-experiments")
    ec.add_argument("ids", nargs="+")
    x = sub.add_parser("export")
    x.add_argument("--format", default="jsonl", choices=["csv", "jsonl"])
    x.add_argument("--audience", default="")
    x.add_argument("--profile", default="")
    x.add_argument("--mode", default="", choices=["", "mock", "live"])
    x.add_argument("--output", type=Path)
    for name in ["candidates", "jobs", "experiments", "spending", "config", "eligibility", "demo"]:
        sub.add_parser(name)
    server = sub.add_parser("serve")
    server.add_argument("--port", type=int, default=8000)
    f = sub.add_parser("forecast")
    f.add_argument("judgment_id")
    f.add_argument("--predictor-id")
    f.add_argument("--task")
    listing = sub.add_parser("predictors")
    listing.add_argument("--judgment-id")
    listing.add_argument("--task")
    m = sub.add_parser("promote")
    m.add_argument("experiment_id")
    m.add_argument("--approved-by", required=True)
    m.add_argument("--rationale", required=True)
    d = sub.add_parser("discovery-errors")
    d.add_argument("experiment_id")
    d = sub.add_parser("discover")
    d.add_argument("experiment_id")
    d.add_argument("proposals", type=Path)
    d.add_argument("--cost-limit", type=float, required=True)
    d.add_argument("--max-rows", type=int, default=100)
    d.add_argument("--max-requests", type=int, default=100)
    d.add_argument("--live", action="store_true")
    d = sub.add_parser(
        "diagnostic-prepare", help="Offline, private snapshot audit and outcome-blind requests"
    )
    d.add_argument("source", type=Path)
    d.add_argument("directory", type=Path)
    d.add_argument("--seed", type=int, default=20260918)
    d.add_argument("--attribution-assumption")
    d = sub.add_parser(
        "diagnostic-prepare-archive", help="Offline private archive version resolution and separate panels"
    )
    d.add_argument("source", type=Path)
    d.add_argument("directory", type=Path)
    d.add_argument("--selection", type=Path, required=True)
    d.add_argument("--context-policy", type=Path, required=True)
    d.add_argument("--seed", type=int, default=20260919)
    d = sub.add_parser(
        "diagnostic-authorize", help="Record separate owner approval; does not call a provider"
    )
    d.add_argument("directory", type=Path)
    d.add_argument("--budget-usd", type=float, required=True)
    d.add_argument("--approved-by", required=True)
    d.add_argument("--note", required=True)
    for name in ("diagnostic-run", "diagnostic-report"):
        d = sub.add_parser(name)
        d.add_argument("directory", type=Path)
    return p


def emit(value, path=None):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    text = value if isinstance(value, str) else json.dumps(value, indent=2, allow_nan=False, default=str)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n")
        print(json.dumps({"written": str(path)}))
    else:
        print(text)


async def run(args, service):
    store = service.store
    c = args.command
    if c.startswith("diagnostic-"):
        from .diagnostics import authorize_diagnostic, prepare_diagnostic, run_diagnostic

        if c == "diagnostic-prepare":
            return prepare_diagnostic(
                args.source,
                args.directory,
                settings=service.settings,
                seed=args.seed,
                attribution_assumption=args.attribution_assumption,
            )
        if c == "diagnostic-prepare-archive":
            from .archive_prepare import prepare_archive_diagnostic

            return prepare_archive_diagnostic(
                args.source,
                args.directory,
                settings=service.settings,
                seed=args.seed,
                selection=json.loads(args.selection.read_text()),
                context_policy=json.loads(args.context_policy.read_text()),
            )
        if c == "diagnostic-authorize":
            return authorize_diagnostic(
                args.directory,
                pilot_ceiling_usd=args.budget_usd,
                approved_by=args.approved_by,
                approval_note=args.note,
            )
        if c == "diagnostic-run":
            return await run_diagnostic(args.directory, settings=service.settings)
        from .diagnostic_report import write_diagnostic_report

        report = write_diagnostic_report(args.directory)
        # The report contains private source text; stdout is a receipt only.
        return {
            "status": "reported",
            "directory": str(args.directory),
            "report_file": "diagnostic_report.md",
            "freeze_hash": report["freeze_hash"],
        }
    if c == "judge":
        if args.input:
            request = JudgeRequest.model_validate_json(args.input.read_text())
        else:
            if args.text is None:
                raise ValueError("Use --text or --input")
            request = JudgeRequest(
                candidate=Candidate(text=args.text), audience_id=args.audience, profile_id=args.profile
            )
        if args.live:
            request.execution_mode = "live"
        if args.references:
            from .state import select_references

            request.context.references = select_references(store, request.candidate, request.context)
        return await service.judge(request)
    if c == "import":
        return import_candidates(
            store,
            args.path.read_text(),
            args.format or args.path.suffix.lstrip("."),
            json.loads(args.mapping),
            args.preview,
            service.settings.max_rows,
        )
    if c == "batch":
        ids = args.ids or [f"{r['candidate_id']}:{r['candidate_version']}" for r in store.list("candidate")]
        job = service.create_job(ids, args.audience, args.profile, "live" if args.live else "mock")
        return await service.run_job(job["job_id"])
    if c == "resume":
        return await service.run_job(args.id)
    if c == "cancel":
        return service.cancel_job(args.id)
    if c == "inspect":
        return service.inspect(args.id)
    if c == "attach-outcomes":
        accepted = []
        errors = []
        for n, row in enumerate(
            read_rows(args.path.read_text(), args.format or args.path.suffix.lstrip(".")), 1
        ):
            try:
                accepted.append(
                    attach_outcome(
                        store, OutcomeObservation.model_validate({k: v for k, v in row.items() if v != ""})
                    )
                )
            except (ValueError, TypeError) as e:
                errors.append({"row": n, "error": str(e)})
        return {"accepted": len(accepted), "errors": errors}
    if c == "compare":
        return await service.compare(
            [JudgeRequest.model_validate(r) for r in json.loads(args.path.read_text())]
        )
    if c in ("evaluate", "preflight", "freeze-evaluation", "readiness"):
        from .contracts import EvaluationRequest
        from .evaluation import evaluate, freeze_evaluation, preflight
        from .readiness import development_readiness

        body = EvaluationRequest(
            synthetic=args.synthetic,
            task=args.task,
            representative_sampling=args.representative_sampling,
            comparison_population=args.population,
            sampling_declaration=args.sampling_declaration,
            max_rows=args.max_rows,
            cohort=json.loads(args.cohort),
        )
        action = {
            "evaluate": evaluate,
            "preflight": preflight,
            "freeze-evaluation": freeze_evaluation,
            "readiness": development_readiness,
        }[c]
        return action(store, **body.model_dump(exclude={"schema_version"}))
    if c == "open-holdout":
        from .contracts import OpenHoldoutRequest
        from .evaluation import open_holdout

        body = OpenHoldoutRequest(frozen_candidate_hash=args.frozen_candidate_hash)
        return open_holdout(store, args.experiment_id, frozen_candidate_hash=body.frozen_candidate_hash)
    if c == "compare-experiments":
        from .evaluation import compare_experiments

        return compare_experiments(store, args.ids)
    if c == "export":
        return export_results(
            store, args.format, audience_id=args.audience, profile_id=args.profile, execution_mode=args.mode
        )
    if c in ("candidates", "jobs", "experiments"):
        return store.list({"candidates": "candidate", "jobs": "job", "experiments": "experiment"}[c])
    if c == "spending":
        return service.account_store.spending()
    if c == "config":
        from .rubric import load_rubric
        from .state import audiences

        return {"audiences": [a.model_dump(mode="json") for a in audiences()], "rubric": load_rubric()}
    if c == "eligibility":
        from .evaluation import eligibility

        return eligibility(store)
    if c == "forecast":
        from .evaluation import predict

        return predict(store, args.judgment_id, predictor_id=args.predictor_id, task=args.task)
    if c == "predictors":
        from .evaluation import compatible_predictors

        return compatible_predictors(store, judgment_id=args.judgment_id, task=args.task)
    if c == "promote":
        from .evaluation import promote

        return promote(store, args.experiment_id, approved_by=args.approved_by, rationale=args.rationale)
    if c == "discovery-errors":
        from .discovery import development_errors

        return development_errors(store, args.experiment_id)
    if c == "discover":
        from .research import run_discovery

        return await run_discovery(
            service,
            args.experiment_id,
            json.loads(args.proposals.read_text()),
            cost_limit_usd=args.cost_limit,
            max_rows=args.max_rows,
            max_requests=args.max_requests,
            live=args.live,
        )
    if c == "demo":
        from .demo import demonstrate

        return await demonstrate(service)
    raise ValueError("Unsupported command")


def main():
    args = parser().parse_args()
    settings = Settings()
    if args.data_dir:
        settings.data_dir = args.data_dir
    if args.command == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port)
        return
    try:
        result = asyncio.run(run(args, Service(settings)))
        emit(result, getattr(args, "output", None))
        if getattr(result, "status", None) == "failed":
            sys.exit(2)
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
