# JevTweet build status

Integration lead: Codex root. Owner-designated remote: `sterlingdigitalp/jevtweet`, branch `main`. Build identity is derived from Git; dirty working builds are identified in judgment lineage.

## Private snapshot diagnostic preparation — implemented and verified offline

Starting from hardened V1 commit `9f8dd1af0eaf1e1f60ff807991093be1bd2ce340`; preserved existing work. The source-specific brief, export and reports are private and excluded from Git. Codex root remains integration lead (contracts, orchestration, service budget reuse, CLI, documentation); parallel owners implemented lossless ingestion, report verification, and permanent research restrictions. Baseline audience/rubric/scoring, promotion thresholds and holdout protections are unchanged. The pilot has **zero live authorization**; the historical verification budget does not authorize this work.

Implemented reusable paths:

- Lossless byte archive, raw CSV columns, opaque outcome-independent identities, honest ingestion timestamps and unwindowed metric sidecars. Explicit context/media exclusions preserve every row without invented authorship, observation windows, author history or sampling.
- Outcome-blind independent requests in a frozen seeded order, pinned code/configuration and descriptive analysis plan, shared runtime cost arithmetic, and an offline private handoff. Separate protocol-bound owner approval and persistent pilot/account limits are required for execution.
- First-terminal-result checkpointing, bounded service retries, pacing, crash recovery, and immutable results before metric joins. No successful/partial/abstained judgment is rerun to match popularity; uncertain interruptions stop with reservations retained. No mock fallback.
- Report verification, full coverage, missingness/ties and descriptive composite/direct-baseline associations only. Private collection requirements preserve the later representative, timestamped, independent-author research assignment and unchanged evaluation cap.
- Permanent private diagnostic membership across identities/close duplicates and datasets sharing the configured account registry; enforced before labels, baselines, references, holdout opening, saved discovery reuse, promotion and forecasts. Public boundary scanner rejects diagnostic artifact filenames.

Reproductions before repairs: missing preparation/ingestion/report APIs failed initial collection; ten restriction regressions initially failed. Four additional behavioral regressions exposed saved-discovery and baseline-only lineage reuse. Independent adversarial tests reproduced freeze-file crash recovery and avoidable local rate-limit failures; both now pass. Nine report integrity regressions exposed prepared-input, request/configuration, fingerprint/SDK/version and source-artifact mismatches; they now pass. A further regression reproduced incompatible returned model identity on a no-answer failure; the runner now rejects it before freezing. All fixtures are original invented data; injected SDK-shaped envelopes test wiring and are not Jev evidence.

Verification completed:

```sh
TYPESAFE_API_KEY='' JEVTWEET_SPEND_LIMIT_USD=0 .venv/bin/pytest -q
.venv/bin/ruff check jevtweet tests scripts
.venv/bin/ruff format --check jevtweet tests scripts
.venv/bin/python scripts/check_public.py
git diff --check
npm run build --prefix frontend
npm run format:check --prefix frontend
TYPESAFE_API_KEY='' JEVTWEET_SPEND_LIMIT_USD=0 JEVTWEET_E2E_LIVE=0 npm test --prefix frontend
bash /tmp/jevtweet-pilot-browser-check.sh
```

Results: **306 backend tests passed**, one opt-in live test skipped; **8 isolated browser tests passed**, five opt-in checks skipped there; **4 actual-backend/mock-provider integration tests passed**. The temporary browser script reproduced CI's disposable backend setup at port 8198 with blank credentials, zero budget, isolated data/account stores and cleanup; its browser command was `JEVTWEET_E2E_URL=http://127.0.0.1:8198 JEVTWEET_E2E_LIVE=0 npm run test:integration --prefix frontend -- --workers=1`. The first sandboxed browser attempt could not bind localhost (`EPERM`); the authorized rerun passed. The upstream Starlette/AnyIO deprecation warning remains.

Focused coverage includes lossless ingestion (16 tests), report bindings/analysis plus full preparation-to-freeze-to-report wiring (40 tests), crash/pacing/authorization/budget/model/first-result handling (13 tests), and permanent membership/reuse controls (19 tests). These are offline tests, including explicitly injected SDK-shaped fixture responses; they establish no Jev semantic result. No pilot provider request, semantic finding, probability model, real final-test opening or promotion has occurred.

The clean-commit offline command is `TYPESAFE_API_KEY='' JEVTWEET_SPEND_LIMIT_USD=0 .venv/bin/jevtweet diagnostic-prepare /path/to/private/export.csv private/corpora/diagnostic_v1`; the source-specific path, audit, exact prospective cost, frozen code identity and reproducibility receipt belong in the ignored private handoff. Separate owner approval is still required before `diagnostic-authorize` or live execution. Existing account authorization is not transferred.

Remaining limitations: snapshot authenticity, sampling, authorship, publication/measurement windows and historical baselines are not supplied by this adapter. Actual rubric diagnosis and descriptive associations remain unavailable until authorized first judgments are frozen. The source is permanently diagnostic-only; a representative, timestamped, independent-author corpus and genuinely new final evidence remain necessary. Shared-registry retention and common account configuration are required across datasets; arbitrary paraphrases and deliberate filesystem/registry bypass are outside the local protection guarantee. No new predictive accuracy, calibration or causal-effect claim has been established. No unresolved failure remains in the tested software scope.

## Focused V1 hardening — implemented and locally verified

Reviewed starting commit `9b361d85be9cdf1a7ca516409a144258f16d0076`; initial working tree was clean. Architecture, rubric, scoring arithmetic and promotion thresholds are preserved. Codex root owns shared contracts, API/CLI, readiness, integration and documentation; the evaluation workstream owns evaluation logic/workflow regressions, the regression workstream owns selection/source/predictor regressions, and the frontend workstream owns browser controls/tests and CI. No live requests were made during this pass; all verification used invented temporary records, test doubles or the explicit mock provider.

Reproduced before behavior changes:

- `.venv/bin/pytest -q tests/test_holdout_workflow.py` — failed: a real evaluation with no declarations consumed a holdout.
- `.venv/bin/pytest -q tests/test_hardening_selection.py` — initially 13 failures and 4 passes; subsequent UTC timestamp-order and post-publication-cutoff regressions each failed before their fixes. Early mock/failed/invalid judgments concealed qualifying live retries, attempt provenance was absent, explicit sources were applied after ambiguity rejection, and global newest predictor selection hid compatible predictors. Ambiguity and impressions rejection already worked and remain protected.
- `.venv/bin/pytest -q tests/test_hardening_api.py` — 4 failures: missing preflight/readiness/opening routes and typed configuration boundary.
- `npm test --prefix frontend -- --grep 'evaluation declarations require' --timeout=6000` — failed before UI edits: no cohort control or staged opening workflow. A second browser regression failed because explicit compatible-predictor selection was absent.
- `.venv/bin/pytest -q tests/test_readiness.py` — 4 failures: development collection planning did not exist.

- Independent review reproduced five further leakage failures before their fixes: opening loaded another holdout frozen later; development-exposed rows could be presented as untouched after earlier corpus growth, re-keying, version changes or near-duplicate copying. Additional tests cover purged test rows, immutable declarations and concurrent opening.
- The browser integration suite initially passed with parallel scheduling, then `--workers=1` reproduced **3 passed / 1 failed** because earlier imports contaminated the staged fixture workflow. Fixed its ordering and serialized the shared disposable backend tests, with no product fallback change.
- A new readiness regression caught an optimistic cap comparison using only successful rows. The cap check now includes observed exclusions and reports both nominal and exclusion-adjusted positive capacity.

Implemented fixes:

- Explicit audience/version/profile/rubric/model/source cohort controls, population, written sampling declaration and attestation. API/CLI/browser preflight validates pre-test configuration and known support shortfalls without reserving or consuming a holdout. Real `evaluate` and `eligibility` are development-only. Freeze stores immutable declarations and development-fitted models; final opening requires the exact frozen hash, atomically consumes once, then reads test outcomes. Insufficient outcomes after opening remain consumed.
- Temporal membership precedes label access. All initially assigned final rows stay protected, including purged rows and aliases. Private development-exposure metadata prevents previously accessed development/history outcomes becoming an untouched final test after corpus changes. Atomic exposure/freeze/open checks include current registries and legacy report provenance; old declarations are not rewritten.
- `earliest_qualifying_configured_attempt_v1` selects by normalized UTC timestamp and stable ID only after execution/configuration/lineage validity checks. It retains every attempt and exclusion, retry recovery and failed-candidate coverage. Explicit source selection governs target labels and historical baselines before ambiguity resolution; impressions never substitute for views.
- Approved predictors resolve by complete compatible configuration and task. Multiple compatible artifacts require explicit selection. Invalid or other-audience artifacts cannot hide valid ones; lineage and promotion checks remain required.
- Development readiness reports observed frequency, positive/negative support, exclusions, staged collection denominators and independent author/content clusters, with collection scenarios and the unchanged 5,000-row cap. It reads no final-test outcomes and makes no power, accuracy or promotion claim.
- Normal CI now starts a disposable loopback backend with an empty `TYPESAFE_API_KEY`, a zero-dollar ceiling and an isolated account directory; runs all four actual-backend/mock-provider browser workflows; then cleans up. No live credentials or private artifact uploads.

Final local verification (all passed):

```sh
TYPESAFE_API_KEY='' JEVTWEET_SPEND_LIMIT_USD=0 .venv/bin/pytest -q
.venv/bin/ruff check jevtweet tests scripts
.venv/bin/ruff format --check jevtweet tests scripts
.venv/bin/python scripts/check_public.py
git diff --check
npm run build --prefix frontend
npm run format:check --prefix frontend
TYPESAFE_API_KEY='' JEVTWEET_SPEND_LIMIT_USD=0 JEVTWEET_E2E_LIVE=0 npm test --prefix frontend
JEVTWEET_E2E_URL=http://127.0.0.1:8197 JEVTWEET_E2E_LIVE=0 npm run test:integration --prefix frontend -- --workers=1
```

Results: **218 backend tests passed**, one opt-in live test skipped; **8 isolated browser tests passed**, five opt-in checks skipped there; **4 actual-backend browser tests passed** separately against a fresh credential-free, zero-budget backend. One upstream Starlette/AnyIO deprecation warning remains. The focused evaluator/reuse group passed 75 tests; the independent selection/adversarial group passed 37. Predictor resolution unit tests explicitly isolate lineage checks, which retain separate artifact/gate regressions. Invented real-mode workflow fixtures lower sample-count gates only within isolated tests; production policy is unchanged.

Reproducible staged CLI demonstration also passed with empty credentials and zero budget:

```sh
.venv/bin/jevtweet --data-dir private/hardening-cli readiness --synthetic --max-rows 160 --output private/hardening-cli-results/readiness.json
.venv/bin/jevtweet --data-dir private/hardening-cli preflight --synthetic --max-rows 160 --output private/hardening-cli-results/preflight.json
.venv/bin/jevtweet --data-dir private/hardening-cli freeze-evaluation --synthetic --max-rows 160 --output private/hardening-cli-results/freeze-evaluation.json
# Values below were read from the preceding private frozen record:
.venv/bin/jevtweet --data-dir private/hardening-cli open-holdout EXPERIMENT_ID --frozen-candidate-hash FROZEN_HASH --output private/hardening-cli-results/opened.json
.venv/bin/jevtweet --data-dir private/hardening-cli spending
```

Observed statuses: `planning_estimate`, `ready`, `frozen`, `evaluated`; synthetic promotion rejected; **zero live requests and $0 charged/reserved**. The full-loop command `TYPESAFE_API_KEY='' JEVTWEET_SPEND_LIMIT_USD=0 JEVTWEET_ACCOUNT_DIR=/tmp/jevtweet-hardening-demo-account .venv/bin/jevtweet --data-dir private/hardening-full-demo demo > private/hardening-full-demo-result.json` also passed and remains part of normal CI. Remote CI status for the pushed milestone is reported in the handoff.

Remaining limitations: no real labeled corpus or predictive research was used, no predictor was promoted, and no new accuracy/calibration/causal-effect claims are established. Exposure tracking is deliberately conservative for outcome bodies accessed in the local database and cannot certify that a human or external tool never inspected outcomes. Unjudged chronological candidates, including baseline-only records, conservatively contribute to collection denominators; inspect staged counts. The unchanged cap can make rare-event research infeasible without a separately versioned resource-policy change. Reports, databases and payloads remain private. No unresolved failing software path remains in the tested scope.

## Initial V1 milestones (historical)

1. **Foundation — complete.** Read the full brief; preserved the only initial local file. Verified primary TypeSafe sources, available SDK 0.7.0, pinned X snapshot and agreement. Locked dependencies; strict v1 contracts, generated schemas, SQLite migration, synthetic fixtures, offline CI.
2. **Vertical slice — implemented.** CLI and browser use the shared service. Real CLI Jev judgment completed, with pinned model identity, deterministic 1–5 editorial score, private persistence and exact run inspector. Actual-backend browser judge/inspect/compare passed with explicit mock mode. Opt-in live browser-to-persistence check passed against the clean committed build; screenshot and response are private.
3. **Corpus machine — complete.** CSV/JSONL mapping/preview, raw private rows and errors, immutable identities, quality reports, temporal reference shortlist, bounded resumable jobs, durable cancellation, shared-account admission/budget reservations, atomic result/run/cache writes, comparisons, escaped CSV/JSONL exports, outcomes and separate annotations.
4. **Evaluation lab — complete software path; predictive evidence absent.** As-of outcome-derived baselines, separate absolute task, common-cohort six-method comparison, chronological selection/calibration/test, maturation/duplicate/thread/author controls, uncertainty/subgroups/reliability plots, frozen source policy, artifact lineage, single-use holdouts and explicit promotion. Forecast correctly unavailable without eligible real evidence.
5. **Research extension — complete.** Reviewed proposals, development-only errors/state, bounded questions/rows/requests/cumulative cost, nonredundancy and incremental-value retention, version comparisons. Actual-backend browser discovery completed on synthetic data without live calls or promotion.
6. **Hardening/handoff — complete.** Independent QA regressions integrated; clean-commit synthetic demo, real CLI/UI integration, public-boundary scan, and remote offline CI all passed.

## Initial V1 verification (historical)

- `UV_CACHE_DIR=/tmp/jevtweet-uv-cache uv sync --locked --offline` — passed.
- `.venv/bin/ruff check jevtweet tests scripts` and `.venv/bin/ruff format --check jevtweet tests scripts` — passed.
- `.venv/bin/pytest -q` — **169 passed**, one live test intentionally skipped; one upstream Starlette/AnyIO deprecation warning.
- `npm ci --prefix frontend`, `npm run build --prefix frontend` — passed; dependency audit reports zero vulnerabilities.
- `npm test --prefix frontend` — **6 passed**, four opt-in browser checks skipped by default.
- `cd frontend && JEVTWEET_E2E_URL=http://127.0.0.1:8001 npm run test:integration` — **3 passed**, actual local backend, explicit mock provider. Exercises paste/persist/inspect/compare, import/batch/export/outcomes/not-ready real evaluation, and synthetic evaluation/discovery.
- `.venv/bin/jevtweet --data-dir private/committed-demo demo` — clean-commit synthetic full-loop demonstration passed; reports and export private. `fixtures/example_mock_result.json` contains only an explicitly marked original synthetic mock result.
- `JEVTWEET_RUN_LIVE=1 JEVTWEET_SPEND_LIMIT_USD=1 .venv/bin/pytest -q tests/test_live.py` — passed. Private sensitivity artifacts cover paraphrase, negation, bait, audience swap, irrelevant context and reference order. No invariance or predictive conclusion asserted.

- `cd frontend && JEVTWEET_E2E_URL=http://127.0.0.1:8001 JEVTWEET_E2E_LIVE=1 npx playwright test tests/live.spec.ts` — **1 passed**, real Jev, exact pinned model, persisted complete editorial result and browser inspector.
- Remote [offline CI](https://github.com/sterlingdigitalp/jevtweet/actions/runs/35411716516) passed on the integrated software milestone, including Linux dependency install, lint/format, backend tests, public scan, frontend build/browser tests and synthetic demo.
- No unresolved failing application path was observed in this verification scope. Rate/budget/auth/schema failures, missing evidence, unavailable outcomes and rejected promotion are explicit tested states, not scores.

## Failures found and repaired

- Initial live wire response failed overly strict score/distribution equality. Versioned joint rounding-interval validation now preserves raw data and accepts only feasible precision differences. Primary documentation discrepancy recorded in research notes.
- Outcome identity compared equivalent UTC encodings incorrectly; normalized and made conflicting concurrent writes atomic.
- Independent QA found process-local cancellation, expiring worker leases, working-directory account resets, malformed-header failures and incomplete final-test promotion checks. Persistent controls/heartbeats, stable account directory, strict client errors and full frozen-cohort checks now have regressions.
- Actual browser test caught export-only fields in batch POST; fixed against the typed API. Earlier browser-install and stale local dependency failures were resolved and rerun.

## Limits and handoff

No real labeled outcome corpus supplied. Predictive accuracy, domain calibration, incremental forecasting benefit, deployment probability validity and causal rewrite effects are **not established**. Synthetic tests demonstrate mechanics; live checks demonstrate integration and retain sensitivity observations privately. No predictor was promoted.

Owner authorized a **$1 total live-verification ceiling**. Persistent private account reservations remain below it; no silent provider substitution. Private payloads, drafts, databases, evaluation/model artifacts and live screenshots are excluded from Git. No credential or software blocker remains for the local V1. Next research step: collect a representative private, timestamped outcome corpus and a genuinely untouched or prospective cohort before making predictive claims. Nonlocal deployment still requires authentication and transport hardening.
