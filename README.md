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

An inspectable [example mock result](fixtures/example_mock_result.json) is included. The demo imports original synthetic candidates, judges them with the mock, resumes an idempotent job, attaches synthetic outcomes, compares variants, exports a CSV, and runs synthetic evaluation mechanics. It makes no paid calls. Outputs remain private.

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
uv run jevtweet readiness --task breakout_48h_v1 --output private/readiness.json
uv run jevtweet preflight --synthetic --max-rows 160
uv run jevtweet freeze-evaluation --synthetic --max-rows 160 --output private/frozen-demo.json
uv run jevtweet open-holdout EXPERIMENT_ID --frozen-candidate-hash EXACT_FROZEN_HASH
uv run jevtweet export --format csv --mode mock --profile text_core_v1 \
  --audience production_ai_coding --output private/ranked.csv
uv run jevtweet discovery-errors EXPERIMENT_ID
uv run jevtweet discover EXPERIMENT_ID fixtures/discovery_proposals.json \
  --cost-limit 0.20 --max-rows 100 --max-requests 100
uv run jevtweet compare-experiments EXPERIMENT_ID_A EXPERIMENT_ID_B
uv run jevtweet forecast JUDGMENT_ID
uv run jevtweet predictors --judgment-id JUDGMENT_ID --task breakout_48h_v1
uv run jevtweet forecast JUDGMENT_ID --predictor-id PREDICTOR_ID --task breakout_48h_v1
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

### Development, freeze, and final-test opening

Real `evaluate` and `eligibility` are development-only. The Experiments workspace has separate **preflight**, **freeze**, and **open final test** actions. Preflight validates declarations and development support without reserving or consuming a holdout. Development access records exposure metadata: rows already inspected cannot become an untouched test by changing the corpus or cohort. Freeze fits on development partitions and persists the candidate and declarations. Opening requires that experiment's exact frozen hash, validates its integrity, then atomically consumes the test before reading its outcomes. Insufficient evidence after opening still consumes it; changing the experiment ID, candidate version or declarations cannot reopen it. Existing evaluated reports are not retrospectively rewritten.

For real preflight/freeze, supply `--cohort` JSON with `audience_id`, `audience_version`, `profile_id`, `rubric_version`, `model_requested`, and `outcome_source`, plus `--population`, `--sampling-declaration`, and `--representative-sampling`. The declaration should describe the collection frame, dates and inclusion/exclusion rules; the checkbox is an attestation, not evidence that sampling was representative. Candidate provenance must also document sampling. The same fields have explicit browser controls. Invalid configuration is rejected before any holdout is reserved or opened. API clients use `POST /api/experiments/preflight`, `/freeze`, and `/{experiment_id}/open-holdout`; the opening body accepts only `frozen_candidate_hash` (and schema version), never revised declarations.

Judgment selection uses `earliest_qualifying_configured_attempt_v1`: filter by declared configuration and required execution mode, validate completion, features and input lineage, then select by UTC `created_at` and judgment ID. Earlier mocks or failures do not conceal a qualifying live retry; later successful retries cannot replace the first qualifying result because of a better score or outcome. All attempts and reasons remain in the audit, including failed candidates and candidates recovered by retry. Outcomes are considered only after judgment selection. An explicit views source filters **both** the target and the as-of historical baseline before label construction; without selection, multiple valid target sources remain ambiguous. Impressions never count as views.

Forecast resolution searches approved, lineage-valid predictors matching the judgment's audience/version, profile, rubric/hash, schema and pinned model, and the requested task when supplied. A newer incompatible predictor cannot hide an older compatible one. If several match, select an explicit predictor ID in the browser or CLI. Selection does not bypass eligibility or promotion checks.

### Collection readiness and the evaluation cap

Before predictive research, run `readiness` with the intended task/cohort or use **Development readiness** in Experiments. It observes development event frequency, positive/negative counts, exclusions, retention and author/content clusters. It estimates collection needs against the existing positive-outcome and unfamiliar-author gates and shows a lower-frequency scenario. It does not read final-test outcomes, fit a predictor, open a holdout, or change thresholds. Reports are private; write CLI results under `private/`.

These are count expectations, not a power calculation or a guarantee of useful predictions. Unknown authors do not count toward independent support, and many posts from a few authors cannot repair a lack of independent authors. Temporal shifts, purges and correlated observations can require substantially more data. Zero observed positives or negatives gives no finite two-class estimate.

The current evaluation cap is **5,000 intended prediction rows before execution and label exclusions**, with a nominal 20% test fraction: at most about 1,000 test rows before exclusions. Unjudged chronological candidates conservatively count toward the collection denominator; inspect the report's staged collection counts, especially if the database also contains baseline-only history. A 1% event frequency gives roughly 10 positives, below the required 40. Even without exclusions, the 40-positive gate implies at least 4% frequency at the cap; the separate 10-positive unfamiliar-author gate can be tighter (about 5% assuming the nominal 20% author holdout). Exclusions increase raw collection needs. If the estimate exceeds the cap, retain editorial-only mode or explicitly revise and version the resource policy before a new prospective evaluation. Do not rebalance outcomes, weaken gates, pool repeated final tests, or inspect test outcomes to tune the plan. The hardening pass does not raise the cap or make new accuracy claims.

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

## Private snapshot diagnostics

Date-only exports can be audited without inventing publication times, authorship,
48-hour outcomes or representative sampling. The reusable snapshot adapter preserves
original bytes and every raw column, stores unwindowed metric sidecars, and prepares
only original posts explicitly marked as having no media. Replies, quotes and posts
with media remain preserved with their missing-context limitations. This path keeps
the baseline audience, profile, questions and scoring unchanged.

Start from a clean committed checkout. The destination must be under `private/` when
inside the repository. Preparation makes no provider requests and authorizes $0:

```sh
TYPESAFE_API_KEY='' JEVTWEET_SPEND_LIMIT_USD=0 .venv/bin/jevtweet diagnostic-prepare /path/to/private/export.csv private/corpora/diagnostic_v1
```

Inspect `OFFLINE_HANDOFF.md`, `data_quality.json`, `protocol.json`, and the request
and prepared-input JSONL files privately. Cost estimates use the same UTF-8 sizing,
pinned pricing, question set and retry allowance as the runtime. They are prospective
reservations, not invoice guarantees. The protocol freezes a seeded, outcome-independent
order, complete cohort, code/configuration and descriptive analysis plan. Metrics,
source rank, filename, dates and source annotations are excluded from model input.
Assessment timestamps record ingestion time; they do not claim pre-publication evidence.

Only after separate owner approval of that exact protocol and its cost estimate:

```sh
.venv/bin/jevtweet diagnostic-authorize private/corpora/diagnostic_v1 --budget-usd APPROVED_CEILING --approved-by OWNER --note APPROVAL_REFERENCE
# Configure TYPESAFE_API_KEY and JEVTWEET_SPEND_LIMIT_USD through the existing environment.
.venv/bin/jevtweet diagnostic-run private/corpora/diagnostic_v1
.venv/bin/jevtweet diagnostic-report private/corpora/diagnostic_v1
```

Authorization is an immutable local approval record, not an authentication system.
An old account ceiling is insufficient. Both the separate pilot ceiling and persistent
shared-account controls apply. The runner paces requests and retains the first terminal
result, including failures, partial results and abstentions. Only the service's bounded
transport retries are allowed; scores and observed popularity never select a retry.
Resume recovers a unique persisted result or processes unstarted candidates. Unknown
interrupted requests stop for review with reservations retained. Completed freezes
cannot be reopened or overwritten. There is no mock fallback.

The report refuses absent/tampered freezes and mock responses. It joins metrics only
after the first-result freeze, shows full coverage and every limitation, and computes
only descriptive rank associations on the fully scored initial subset. It does not
fit probabilities, tune the rubric, claim accuracy, or promote predictors.

Every imported identity is permanently diagnostic-only in private local and shared
account registries. Evaluation, baselines, reference lineage, saved discovery reports,
final tests, predictor promotion and forecasts enforce this restriction across rekeys
and close duplicates. Keep those registries and use the same `JEVTWEET_ACCOUNT_DIR`
across datasets. Deliberately deleting registries, changing accounts, or arbitrary
paraphrases outside the existing duplicate policy cannot be certified by this local
application. Newly collected representative, timestamped, independent-author data
and an untouched later cohort are still required; the 5,000-row cap and promotion
thresholds remain unchanged. Keep all actual exports, reports, protocol files and
judgments out of Git. Tests use original invented records only.


## Private archive diagnostics

For a supplied versioned ZIP, declare each input file's role in a private selection
JSON (`files` maps manifest-relative paths to `primary_content`, `metrics`,
`gapfill_content`, `gapfill_metrics`, historical content/metrics/summary, context,
summary or ancillary roles). Primary content wins over equivalent gapfill duplicates;
substantive target/context differences remain quarantined. Dedicated context requires
an exact source identity. Declare source priorities and justified version/path
overrides in a private context policy; overrides never waive missing text/media/time
limitations. Historical copies are preserved without becoming target additions.

```sh
TYPESAFE_API_KEY='' JEVTWEET_SPEND_LIMIT_USD=0 .venv/bin/jevtweet diagnostic-prepare-archive /path/to/private/source.zip private/corpora/archive_v1 --selection /path/to/private/selection.json --context-policy /path/to/private/context_policy.json
```

The supplied manifest and archive remain immutable. Every payload checksum/size is
verified; a stale self-manifest entry is recorded explicitly rather than repaired in
place. Derived manifests exclude their own bytes. Metrics join by post ID from JSONL;
blank summary cells remain missing, views never become impressions, duplicate
observation-time claims do not imply separate observations or growth. Metrics-only
IDs stay unjudgeable. All target, context, historical and orphan identities are
permanently restricted to diagnostics.

Panel A contains eligible originals without indicated media. Panel B separately
contains eligible quotes with resolved supplied-text context. Unknown source media
completeness is retained as a limitation; known material media gaps, partial articles,
conflicts and absent text remain quarantined. This is an assessment of text observed
at ingestion. Claimed publication times are private provenance, never fabricated
pre-publication availability. Quoted replies retain their exact ID and text; a
separate parent is labeled as additional context. Continuations retain their own
timestamps and are not concatenated into a quoted source. Collector objects and nested
outcome metadata cannot enter provider state; authentic numbers in text remain intact.

Inspect the frozen panel/source manifests, version decisions, exclusions, requests,
input hashes, order and reservation estimate before requesting new protocol-specific
authorization. Use the existing `diagnostic-authorize`, `diagnostic-run`, then
`diagnostic-report` workflow above. An earlier pilot's approval does not authorize a
new protocol. Post-freeze reports retain panel/account/month and joint strata, with
undefined small-group associations and complete failure/exclusion coverage. No pooled
panel accuracy claim, predictor training, promotion or retrospective rubric tuning is
performed. All supplied archives and source-specific reports belong outside Git.
