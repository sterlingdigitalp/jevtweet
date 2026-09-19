"""Loopback-only HTTP surface. API and CLI share Service."""

import asyncio
import html
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .contracts import (
    Annotation,
    CompareRequest,
    DiscoveryRequest,
    EvaluationRequest,
    ExperimentComparisonRequest,
    ImportRequest,
    JobRequest,
    JudgeRequest,
    OutcomeObservation,
    PromotionRequest,
)
from .corpus import attach_outcome, export_results, import_candidates, quality_report, read_rows
from .service import Service
from .settings import Settings
from .state import audiences


def create_app(settings: Settings | None = None, service: Service | None = None):
    service = service or Service(settings)
    app = FastAPI(
        title="JevTweet",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        description="Editorial potential — not a calibrated probability. Loopback only.",
    )
    app.state.service = service
    token = secrets.token_urlsafe(32)
    background = set()
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin:
            try:
                parsed = urlsplit(origin)
                allowed = (
                    parsed.scheme == "http"
                    and parsed.hostname in ("localhost", "127.0.0.1", "::1")
                    and parsed.port in (request.url.port or 80, 5173)
                    and not parsed.username
                    and not parsed.password
                )
            except ValueError:
                allowed = False
            if not allowed:
                return JSONResponse({"detail": "Origin not allowed"}, 403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Cross-site requests rejected"}, 403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            supplied = request.headers.get("x-csrf-token", "")
            if not secrets.compare_digest(supplied, token):
                return JSONResponse({"detail": "Valid local session CSRF token required"}, 403)
        try:
            length = int(request.headers.get("content-length", "0"))
            if length < 0:
                raise ValueError("Negative content length")
        except ValueError:
            return JSONResponse({"detail": "Invalid Content-Length"}, 400)
        if length > 10_000_000:
            return JSONResponse({"detail": "Request exceeds 10 MB"}, 413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({"detail": str(exc)}, 400)

    @app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
    def local_docs():
        schema = app.openapi()
        rows = []
        for path, operations in schema["paths"].items():
            for method, operation in operations.items():
                if method not in {"get", "post", "put", "delete", "patch"}:
                    continue
                body = (
                    operation.get("requestBody", {})
                    .get("content", {})
                    .get("application/json", {})
                    .get("schema", {})
                )
                rows.append(
                    f"<tr><td>{method.upper()}</td><td>{html.escape(path)}</td><td>{html.escape(operation.get('summary', ''))}</td><td><code>{html.escape(body.get('$ref', '').split('/')[-1])}</code></td></tr>"
                )
        return (
            "<!doctype html><html lang='en'><meta charset='utf-8'><title>JevTweet local API</title><body><h1>JevTweet API</h1><p>Loopback only. Fetch /api/session, then send its csrf_token in X-CSRF-Token on every mutation. Default execution is mock; live requires explicit mode and a configured budget.</p><p><a href='/openapi.json'>Complete OpenAPI schemas, parameters and response contracts</a></p><table><thead><tr><th>Method</th><th>Path</th><th>Operation</th><th>JSON request schema</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table></body></html>"
        )

    @app.get("/api/session")
    def session():
        from .evaluation import forecast_status

        return {
            "csrf_token": token,
            "live_configured": bool(os.getenv("TYPESAFE_API_KEY")) and service.settings.spend_limit_usd > 0,
            "spending": service.account_store.spending(),
            "spend_limit_usd": service.settings.spend_limit_usd,
            "forecast": forecast_status(service.store),
        }

    @app.get("/api/config")
    def config():
        from .rubric import load_rubric

        return {
            "audiences": [a.model_dump(mode="json") for a in audiences()],
            "rubric": load_rubric(),
            "limits": {
                "max_rows": service.settings.max_rows,
                "concurrency": service.settings.concurrency,
                "model": service.settings.model,
                "spend_limit_usd": service.settings.spend_limit_usd,
            },
        }

    @app.post("/api/references")
    def references(body: JudgeRequest):
        from .state import select_references

        return [
            r.model_dump(mode="json") for r in select_references(service.store, body.candidate, body.context)
        ]

    @app.post("/api/judge")
    async def judge(body: JudgeRequest):
        return await service.judge(body)

    @app.post("/api/compare")
    async def compare(body: CompareRequest):
        return await service.compare(body.requests)

    @app.get("/api/judgments")
    def judgments():
        return service.store.list("judgment")

    @app.get("/api/judgments/{judgment_id}")
    def inspect(judgment_id: str):
        return service.inspect(judgment_id)

    @app.get("/api/candidates")
    def candidates():
        return service.store.list("candidate")

    @app.get("/api/corpus/quality")
    def quality():
        return quality_report(service.store.list("candidate"), service.store.list("outcome"))

    @app.post("/api/import")
    def import_rows(body: ImportRequest):
        return import_candidates(
            service.store, body.content, body.format, body.mapping, body.preview, service.settings.max_rows
        )

    def launch(job_id):
        if any(getattr(t, "job_id", None) == job_id for t in background):
            raise ValueError("Job already running")
        task = asyncio.create_task(service.run_job(job_id))
        task.job_id = job_id
        background.add(task)

        def complete(t):
            background.discard(t)
            try:
                t.result()
            except (asyncio.CancelledError, Exception):
                pass

        task.add_done_callback(complete)

    @app.post("/api/jobs")
    async def job(body: JobRequest):
        job = service.create_job(body.candidate_ids, body.audience_id, body.profile_id, body.execution_mode)
        launch(job["job_id"])
        return job

    @app.get("/api/jobs")
    def jobs():
        return service.store.list("job")

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        job = service.store.get("job", job_id)
        if not job:
            raise HTTPException(404, "Unknown job")
        return job

    @app.post("/api/jobs/{job_id}/resume")
    async def resume(job_id: str):
        job = get_job(job_id)
        launch(job_id)
        return job

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        return service.cancel_job(job_id)

    @app.get("/api/export")
    def export(
        format: str = "jsonl",
        audience_id: str = "",
        profile_id: str = "",
        execution_mode: str = "",
        audience_version: str = "",
        rubric_version: str = "",
    ):
        data = export_results(
            service.store,
            format,
            audience_id=audience_id,
            profile_id=profile_id,
            execution_mode=execution_mode,
            audience_version=audience_version,
            rubric_version=rubric_version,
        )
        return PlainTextResponse(
            data,
            media_type="text/csv" if format == "csv" else "application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="jevtweet.{format}"'},
        )

    @app.post("/api/outcomes")
    def outcome(body: OutcomeObservation):
        return attach_outcome(service.store, body)

    @app.get("/api/outcomes")
    def outcomes():
        return service.store.list("outcome")

    @app.post("/api/outcomes/import")
    def outcome_import(body: ImportRequest):
        accepted = []
        errors = []
        for n, row in enumerate(read_rows(body.content, body.format, service.settings.max_rows), 1):
            try:
                accepted.append(
                    attach_outcome(
                        service.store,
                        OutcomeObservation.model_validate({k: v for k, v in row.items() if v != ""}),
                    )
                )
            except (ValueError, TypeError) as exc:
                errors.append({"row": n, "error": str(exc)})
        record = {"accepted": len(accepted), "errors": errors}
        return record

    @app.post("/api/annotations")
    def annotation(body: Annotation):
        if not service.store.get("candidate", f"{body.candidate_id}:{body.candidate_version}"):
            raise ValueError("Unknown candidate")
        return service.store.put("annotation", body.annotation_id, body)

    @app.get("/api/experiments")
    def experiments():
        return service.store.list("experiment")

    @app.get("/api/experiments/{experiment_id}")
    def experiment(experiment_id: str):
        result = service.store.get("experiment", experiment_id)
        if not result:
            raise HTTPException(404, "Unknown experiment")
        return result

    @app.post("/api/experiments")
    async def evaluate(body: EvaluationRequest):
        from .evaluation import evaluate

        return await asyncio.to_thread(
            evaluate,
            service.store,
            synthetic=body.synthetic,
            task=body.task,
            representative_sampling=body.representative_sampling,
            comparison_population=body.comparison_population,
            cohort=body.cohort,
        )

    @app.post("/api/experiments/compare")
    def compare_experiments(body: ExperimentComparisonRequest):
        from .evaluation import compare_experiments

        return compare_experiments(service.store, body.experiment_ids)

    @app.get("/api/eligibility")
    def eligibility(task: str = "breakout_48h_v1"):
        from .evaluation import eligibility

        return eligibility(service.store, task=task)

    @app.post("/api/experiments/{experiment_id}/promote")
    def promote(experiment_id: str, body: PromotionRequest):
        from .evaluation import promote

        return promote(service.store, experiment_id, approved_by=body.approved_by, rationale=body.rationale)

    @app.get("/api/forecast/{judgment_id}")
    def forecast(judgment_id: str):
        from .evaluation import predict

        return predict(service.store, judgment_id)

    @app.get("/api/discovery/{experiment_id}/errors")
    def development_errors(experiment_id: str):
        from .discovery import development_errors

        return development_errors(service.store, experiment_id)

    @app.post("/api/discovery")
    async def discover(body: DiscoveryRequest):
        from .research import run_discovery

        return await run_discovery(
            service,
            body.experiment_id,
            body.proposals,
            cost_limit_usd=body.cost_limit_usd,
            max_rows=body.max_rows,
            max_requests=body.max_requests,
            live=body.live,
        )

    dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/")
        def index():
            return FileResponse(dist / "index.html")
    else:

        @app.get("/")
        def index():
            return {
                "message": "Build frontend with npm ci && npm run build in frontend/, or run Vite at localhost:5173.",
                "docs": "/docs",
            }

    return app
