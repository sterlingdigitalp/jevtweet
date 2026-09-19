# JevTweet

A local-first Jev tweet judge, corpus processor, and evaluation lab. **Editorial potential — not a calibrated probability.** The default deterministic mock is a software demonstration. Select live mode explicitly to use the pinned TypeSafe model. Forecasts remain unavailable until real outcomes satisfy the recorded evaluation policy and an owner explicitly approves promotion.

## Run

Requires Python 3.12+, Node 22+, and [uv](https://docs.astral.sh/uv/). No X account, GPU, Redis, or remote database is required.

```sh
uv sync --locked --python 3.12
cd frontend
npm ci
npm run build
cd ..
uv run jevtweet serve
```

Open <http://127.0.0.1:8000>. API documentation: <http://127.0.0.1:8000/docs>. Judge, Corpus, and Experiments use the same service as the CLI. For frontend development run `npm run dev` in `frontend`; Vite proxies `/api` to port 8000.

```sh
uv run pytest -q
uv run python scripts/check_public.py
mkdir -p private
uv run jevtweet --data-dir private/demo demo > private/demo-result.json
```

The demo imports original synthetic candidates, judges them with the mock, resumes an idempotent job, attaches synthetic outcomes, compares variants, exports a CSV, and runs synthetic evaluation mechanics. It makes no paid calls. Outputs remain private.

## Live execution and spending

Export `TYPESAFE_API_KEY` in the environment. Never paste it into the browser, an import, or a command argument. `.env.example` documents settings; dotenv files are **not automatically loaded**.

```sh
export JEVTWEET_SPEND_LIMIT_USD=1
export JEVTWEET_ACCOUNT_DIR="$PWD/private/account"
uv run jevtweet --data-dir private/live judge --live \
  --text 'Our migration replay caught an index drop before merge.' \
  --output private/live/result.json
```

The default account directory is resolved from the installed application location, so changing the working directory does not reset it. Set an absolute `JEVTWEET_ACCOUNT_DIR` to share accounting across installations. The account directory is shared across local corpora and processes. Keep every process using the same account directory and concurrency settings; moving or deleting it resets local accounting. It does not control spending by unrelated software or provider accounts. Each attempt reserves a conservative UTF-8-byte-based input estimate before sending. Actual input usage reconciles the estimate. Unknown network outcomes retain the reservation; retries may be billed twice even when persisted results are idempotent. Pricing is dated configuration, not a billing guarantee. Default ceiling is zero; no live-to-mock fallback exists.

Default concurrency is 2, account admission limit 30 requests/minute, request timeout 45 seconds, at most 3 application attempts, SDK retries disabled. Budget/rate stops are explicit and resumable. Live requests use the fixed official HTTPS endpoint; an unrelated SDK base-URL override cannot redirect private inputs.

## Commands

```sh
uv run jevtweet judge --text 'Paste a draft here'
uv run jevtweet judge --input private/request.json --live
uv run jevtweet import fixtures/candidates_synthetic.jsonl --preview
uv run jevtweet import fixtures/candidates_synthetic.jsonl
uv run jevtweet import private/posts.csv --mapping '{"text":"body","candidate_id":"post_id"}'
uv run jevtweet batch --profile text_core_v1 --audience production_ai_coding
uv run jevtweet resume JOB_ID
uv run jevtweet cancel JOB_ID
uv run jevtweet inspect JUDGMENT_ID
uv run jevtweet compare private/variant_requests.json
uv run jevtweet attach-outcomes fixtures/outcomes_synthetic.jsonl
uv run jevtweet eligibility
uv run jevtweet evaluate --synthetic --output private/synthetic-evaluation.json
uv run jevtweet evaluate --task breakout_48h_v1 --output private/evaluation.json
uv run jevtweet export --format csv --mode mock --profile text_core_v1 \
  --audience production_ai_coding --output private/ranked.csv
uv run jevtweet discovery-errors EXPERIMENT_ID
uv run jevtweet discover EXPERIMENT_ID fixtures/discovery_proposals.json \
  --cost-limit 0.20 --max-rows 100 --max-requests 100
uv run jevtweet compare-experiments EXPERIMENT_ID_A EXPERIMENT_ID_B
uv run jevtweet forecast JUDGMENT_ID
uv run jevtweet promote EXPERIMENT_ID --approved-by OWNER --rationale 'Reviewed evidence and deployment population'
uv run jevtweet spending
```

Global `--data-dir PATH` precedes the subcommand. `judge --references` explicitly selects earlier relevant corpus text; all known test posts, future material, the target, near duplicates, and its thread are excluded. Supply custom as-of reference records in a full request when a curated set is preferable. `reference_enriched_v1` requires at least three adequate references; missing references never imply originality. An explicit new core-profile judgment is the supported fallback.

CLI JSON results preserve decimal scores and raw factor distributions. `inspect` includes exact model-facing state, questions, lineage, calculation, attempts and versions; secrets and private malformed-response diagnostics are excluded from HTTP inspectors. Full diagnostic payloads stay in the private database. The SDK and model are pinned; malformed or failed answers are unavailable, never low ratings.

## Data contracts

Canonical strict v1 Pydantic schemas are in `jevtweet/contracts.py`; the HTTP OpenAPI schema is generated from them. Unknown schema versions are rejected. `uv run python scripts/export_schemas.py` regenerates the public JSON schemas. All timestamps require an explicit UTC offset and are normalized to UTC. Candidate identity is `(candidate_id, candidate_version)`. Content versions and outcome measurements are immutable: create a new version for changed content. Import mapping is canonical field → source column. CSV/JSONL imports retain original rows and validation errors privately; unknown columns never reach Jev.

Minimal import:

```json
{"candidate_id":"example","text":"Original synthetic example","content_available_at":"2026-01-01T12:00:00Z","published_at":"2026-01-01T12:00:00Z","author_id":"example-author","distribution":"organic","synthetic":true}
```

A JSONL row may instead contain `{ "candidate": {...}, "context": {...} }`. Historical evaluation requires `context.prediction_cutoff`, timestamped historical metadata and genuinely as-of text/context. A publication timestamp is the conservative import default for content availability when no snapshot timestamp was supplied; verify it against your source. Parent/quoted content and media descriptions use `{text, occurred_at, available_at, provenance}`. Media is text-only to Jev; the application never downloads tweet URLs or implies it saw an image.

An outcome uses `candidate_id`, `candidate_version`, `source`, `metric` (`views` or `impressions`), `observed_at`, `available_at`, `elapsed_hours`, nullable `views`/engagement counts, distribution conditions, and provenance. Missing counts stay null. Views and impressions are never silently mixed. Human annotations are a separate record kind/API endpoint and never virality labels.

## Meaning and evaluation

Editorial scoring has one backend implementation. Positive scores divide by four, use equal weights, then subtract `0.25 × aversion/4`; the result maps to 1–5 with half-up rounding. Answer certainty never multiplies quality. Missing required factors or essential context prevent a complete headline. Explanations describe rubric contributions, not causal reach improvements. Compare/rank within the same audience/version, profile, rubric and execution mode; different reference sets remain an explicit limitation.

`breakout_48h_v1`: at least 10,000 observed views **and** at least 10× the median of up to the previous 20 comparable organic posts. At least 10 qualifying observations must have been available at prediction time. The observation window is 48 ± 1 hour. Zero/missing baselines are unavailable. `absolute_48h_v1` is a distinct cold-start reach task.

Evaluation compares constant prevalence, direct Jev, calibrated editorial score, metadata-only, Jev-plus-metadata, and Jev-only models on a common eligible cohort. Chronological train/selection/calibration/test boundaries, outcome maturation, duplicate/thread purging, author holdout, fold-local transformations and calibration are recorded. Reports include ranking and probability metrics, reliability data, subgroups, cluster-resampled uncertainty, bands, failures, coverage and exclusions. Undefined metrics remain null. Raw editorial scores are never probabilities.

The frozen promotion policy requires representative sampling, adequate independent real outcomes, calibration, incremental utility over metadata, uncertainty support, and manual approval. Synthetic runs cannot promote. Identical experiment replay returns its frozen report; a changed candidate cannot reuse an opened real holdout. Forecast tier boundaries are versioned probabilities, not within-batch ranks; tier 5 starts at 35%, not 80%.

Reviewed feature proposals can add narrow Score/Noul questions on development-only state. Caps: five iterations, eight proposals per iteration, 24 active semantic features, explicit row/request/cumulative-cost limits. Retention requires incremental development value and nonredundancy. Research never promotes production automatically; accepted features require a new untouched holdout.

## Local security and private artifacts

The server binds to `127.0.0.1`. Host/origin checks and a local session CSRF token protect mutations. Clients fetch `/api/session` and send `X-CSRF-Token`; CLI calls the shared service directly. Tweet HTML is rendered as text. Spreadsheet CSV exports escape formula prefixes. Nonlocal deployment requires authentication and TLS work and is outside this local V1.

`private/`, databases, `.env`, payloads, logs, trained artifacts, and real evaluation reports are ignored. Public fixtures are original synthetic examples. Do not put owner corpora in `fixtures/`. Real service-performance and predictive artifacts stay private pending owner review and any required TypeSafe permission. Source references, contract discrepancies and limitations are in `RESEARCH_NOTES.md`; execution evidence and remaining work are in `BUILD_STATUS.md`.

Opt-in live verification (outputs only under the private directory):

```sh
JEVTWEET_RUN_LIVE=1 JEVTWEET_SPEND_LIMIT_USD=1 uv run pytest -q tests/test_live.py
```

This checks provider integration and records sensitivity to paraphrase, negation, bait, audience, irrelevant context, and reference order. It does not assert invariance or establish prediction accuracy. Use `jevtweet serve --port 8001` if port 8000 is occupied.

Actual browser workflow verification uses explicit mock judgments against your private local server:

```sh
cd frontend
JEVTWEET_E2E_URL=http://127.0.0.1:8001 npm run test:integration
```

For the separate paid live browser smoke, start that server with its spending ceiling, then explicitly opt in:

```sh
JEVTWEET_E2E_URL=http://127.0.0.1:8001 JEVTWEET_E2E_LIVE=1 npx playwright test tests/live.spec.ts
```

Live screenshots/results stay in `private/live-ui/`. Install Chromium once with `npx playwright install chromium` if needed. The normal `npm test` suite skips all external-server/live tests.
