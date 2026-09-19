# JevTweet build status

Integration lead: Codex root. Owner-designated remote: `sterlingdigitalp/jevtweet`, branch `main`. Build identity is derived from Git; dirty working builds are identified in judgment lineage.

## Milestones

1. **Foundation — complete.** Read the full brief; preserved the only initial local file. Verified primary TypeSafe sources, available SDK 0.7.0, pinned X snapshot and agreement. Locked dependencies; strict v1 contracts, generated schemas, SQLite migration, synthetic fixtures, offline CI.
2. **Vertical slice — implemented.** CLI and browser use the shared service. Real CLI Jev judgment completed, with pinned model identity, deterministic 1–5 editorial score, private persistence and exact run inspector. Actual-backend browser judge/inspect/compare passed with explicit mock mode. Final opt-in live browser check pending.
3. **Corpus machine — complete.** CSV/JSONL mapping/preview, raw private rows and errors, immutable identities, quality reports, temporal reference shortlist, bounded resumable jobs, durable cancellation, shared-account admission/budget reservations, atomic result/run/cache writes, comparisons, escaped CSV/JSONL exports, outcomes and separate annotations.
4. **Evaluation lab — complete software path; predictive evidence absent.** As-of outcome-derived baselines, separate absolute task, common-cohort six-method comparison, chronological selection/calibration/test, maturation/duplicate/thread/author controls, uncertainty/subgroups/reliability plots, frozen source policy, artifact lineage, single-use holdouts and explicit promotion. Forecast correctly unavailable without eligible real evidence.
5. **Research extension — complete.** Reviewed proposals, development-only errors/state, bounded questions/rows/requests/cumulative cost, nonredundancy and incremental-value retention, version comparisons. Actual-backend browser discovery completed on synthetic data without live calls or promotion.
6. **Hardening/handoff — final verification.** Independent QA regressions integrated. Final live UI, public scan, pushed CI and final clean-commit demo remain to verify.

## Executed verification

- `UV_CACHE_DIR=/tmp/jevtweet-uv-cache uv sync --locked --offline` — passed.
- `.venv/bin/ruff check jevtweet tests scripts` and `.venv/bin/ruff format --check jevtweet tests scripts` — passed.
- `.venv/bin/pytest -q` — **169 passed**, one live test intentionally skipped; one upstream Starlette/AnyIO deprecation warning.
- `npm ci --prefix frontend`, `npm run build --prefix frontend` — passed; dependency audit reports zero vulnerabilities.
- `npm test --prefix frontend` — **6 passed**, four opt-in browser checks skipped by default.
- `cd frontend && JEVTWEET_E2E_URL=http://127.0.0.1:8001 npm run test:integration` — **3 passed**, actual local backend, explicit mock provider. Exercises paste/persist/inspect/compare, import/batch/export/outcomes/not-ready real evaluation, and synthetic evaluation/discovery.
- `.venv/bin/jevtweet --data-dir private/final-demo demo` — synthetic full-loop demonstration; reports and export private.
- `JEVTWEET_RUN_LIVE=1 JEVTWEET_SPEND_LIMIT_USD=1 .venv/bin/pytest -q tests/test_live.py` — passed. Private sensitivity artifacts cover paraphrase, negation, bait, audience swap, irrelevant context and reference order. No invariance or predictive conclusion asserted.

## Failures found and repaired

- Initial live wire response failed overly strict score/distribution equality. Versioned joint rounding-interval validation now preserves raw data and accepts only feasible precision differences. Primary documentation discrepancy recorded in research notes.
- Outcome identity compared equivalent UTC encodings incorrectly; normalized and made conflicting concurrent writes atomic.
- Independent QA found process-local cancellation, expiring worker leases, working-directory account resets, malformed-header failures and incomplete final-test promotion checks. Persistent controls/heartbeats, stable account directory, strict client errors and full frozen-cohort checks now have regressions.
- Actual browser test caught export-only fields in batch POST; fixed against the typed API. Earlier browser-install and stale local dependency failures were resolved and rerun.

## Limits and next step

No real labeled outcome corpus supplied. Predictive accuracy, domain calibration, incremental forecasting benefit, deployment probability validity and causal rewrite effects are **not established**. Synthetic tests demonstrate mechanics; live checks demonstrate integration and retain sensitivity observations privately. No predictor was promoted.

Owner authorized a **$1 total live-verification ceiling**. Persistent private account reservations remain below it; no silent provider substitution. Private payloads, drafts, databases, evaluation/model artifacts and live screenshots are excluded from Git. Remaining step: finish clean-commit browser/live/CI verification and public-boundary review.
