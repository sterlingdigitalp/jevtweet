# JevTweet V1 — Build the judging machine

**Repository:** `sterlingdigitalp/jevtweet`  
**Brief date:** September 18, 2026  
**Mission:** Build a working, local-first system that uses TypeSafe’s Jev to judge tweet candidates, rank a corpus, and test whether its judgments predict real performance.

You are an excellent engineering team with parallel coding agents. Build the product, not just the architecture or a wrapper around an API. Make strong implementation decisions, keep the system small enough to understand, and demonstrate every completed path. Inspect the local repository before changing anything; GitHub was empty when this brief was prepared, but local work may already exist. Preserve unrelated work.

The owner’s working style is: establish a roadmap, build rapidly in parallel, test, revise the roadmap from evidence, and repeat. Use this brief as the initial roadmap. Do not block on reversible decisions. Record assumptions; ask only for genuinely necessary credentials, permissions, or irrecoverable product choices.

## 1. Product definition and non-negotiable distinctions

The user-facing question is:

> **“Will this tweet go viral?” Rate it 1–5: 1 very low, 2 low, 3 moderate, 4 high, 5 very high.**

Build two explicitly different modes:

**Editorial mode — available immediately.** A rubric-based assessment of spreading potential for a specified audience. Show “Editorial potential — not a calibrated probability.” This is the default until an outcome model earns promotion.

**Forecast mode — implemented, but evidence-gated.** An estimated probability of a precisely defined breakout event, with an associated 1–5 tier. Keep it unavailable until suitable real outcomes, calibration, and an untouched evaluation justify it. Training a model does not automatically authorize this mode.

Keep editorial rating, probability of breakout, Jev’s answer certainty, evidence completeness, and operational success separate. A confident Jev judgment is not a confident forecast. A failed request is not a score of 1. Missing data is not zero. A mock response is not a live response. A promising rewrite is not evidence of a causal improvement in reach.

Do not claim to reproduce X’s production recommendations. Build a testable content judge informed by X, not a supposed deterministic “viral formula.”

## 2. Source facts to carry into the implementation

Verify these against the primary references at the end before writing the adapter. If a source has changed, record the discrepancy, pin the new contract, and adapt deliberately.

### Jev and the TypeSafe interface

The documentation currently names `jev-1.13.0`. Pin a version, not `jev-latest`. Jev is text-only; preprocess media separately. Customer-specific fine-tuning/LoRA is not offered; downstream supervised models consuming Jev features are explicitly documented. [S1]

Use the official Python SDK, whose September 18 changelog documents `0.7.0`; verify the available distribution and lock the tested version. Version 0.6 changed `Score.criteria` to an ordered sequence, and 0.7 changed serialization to Pydantic. Do not copy outdated examples. [S2]

The API uses `POST https://api.typesafe.ai/v1/systemone`, bearer authentication, and `state`, `model`, and `questions`. Use the documented SDK adapter rather than inventing fields. [S3]

Primitive contracts: [S4–S6]

| Primitive | Purpose | Returned information |
|---|---|---|
| Score | Position among descriptive, ordered levels | Score, level probabilities, legend, confidence |
| Choice | Selection among named alternatives | Choice, option probabilities, confidence |
| Noul | Yes/no semantic judgment | `noul` in [0,1]; no separate confidence field |

Five Score criteria produce a zero-based value in [0,4], possibly fractional. The criteria—not their IDs or ordinal labels—must explain the question completely. Question IDs are not supplied to the model. Questions sharing a request see the same state and are evaluated independently; dependent judgments require a subsequent request. Preserve distributions rather than retaining only the mean. [S4, S5]

The models page currently documents 64k total request tokens and 32k for state plus the longest question; some overview text is less specific. Use conservative configurable limits, verify the actual contract, and do not design around filling the context window. Current published pricing is $0.042 per million input tokens, with output tokens free; rates and service limits must remain dated configuration, not business guarantees. [S1]

Keep arithmetic, counting, time comparisons, normalization, and control flow in code. Jev’s limitations include literal interpretation, irrelevant-context degradation, adversarial state, and unreliable numeric reasoning. It is not a prose generator. [S7]

Read the official TypeSafe agent skill and its references; inspect any installer before using it. Centralize questions, thresholds, and criteria. [S8]

### X algorithm reference snapshot

Use this verified snapshot as the starting reference, not an assertion about every deployed experiment:

```text
Repository: xai-org/x-algorithm
Commit: c279172eb8a992170f077d4c1d441923ef215d2e
Commit timestamp: 2026-09-18T01:07:14Z
Parameter-file sync note: 2026-09-17T20:46:44Z
Parameter file: home-mixer/params/param.rs
Scorer: home-mixer/scorers/ranking_scorer.rs
Architecture: phoenix/README.md
```

Selected published base coefficients: [S9]

| Predicted action | Coefficient |
|---|---:|
| Like / Favorite | 0.5 |
| Repost / Retweet | 1 |
| Reply | 5 |
| Quote | 5 |
| Share | 2 |
| Share via direct message | 5 |
| Share via copied link | 20 |
| Follow author | 4 |
| Not interested | -43.2 |
| Block author | -31.2 |
| Mute author | -58.8 |
| Report | -234 |

These multiply predicted viewer actions, not observed engagement counts. The scorer includes additional signals and conditional adjustments; these twelve coefficients are not the entire ranking system. Phoenix combines retrieval and personalized ranking, and the released code does not supply X’s production checkpoint or private behavioral corpus. [S9–S11]

Store this information in a dated, machine-readable reference configuration with source paths and commit. Map it to hypotheses: forwarding value, conversation value, sustained attention, follow value, and audience aversion. **Never multiply Jev’s semantic feature scores by these coefficients and label the result “X’s score.”** Do not import unsupported folklore about hashtags, links, post length, or supposed fixed engagement multipliers.

## 3. The V1 that must actually work

Deliver this end-to-end loop:

```text
Paste/import candidates + select audience
    → validate and construct prediction-time-safe state
    → Jev: independent semantic judgments
    → deterministic editorial scoring and review flags
    → inspect, compare, rank, and export
    → attach later outcome observations
    → run leakage-safe experiments
    → compare baselines and candidate predictors
    → promote only with evidence and explicit approval
```

Support single tweets, corpus batches, and side-by-side comparisons of user-supplied variants. Build a real web interface, CLI, persisted jobs, inspectable results, and evaluation commands. Do not require X credentials for V1. Manual inputs and CSV/JSONL imports are sufficient.

Seed editable audience configurations for production AI coding, building-in-public/indie SaaS, humanoid robotics, AI workflows for SMBs, and no-code/AI app building. Make production AI coding the initial default. Each persona needs a concrete audience description, interests, assumed knowledge, and relevant examples—not just a niche name. These are starting configurations, not validated descriptions of actual followers.

Preferred architecture: Python backend with typed schemas, FastAPI, SQLite with migrations, an asynchronous TypeSafe adapter, and a React/TypeScript frontend. Use the same application service layer from API and CLI. A different choice needs a concrete advantage, not another framework. Keep training CPU-friendly; do not make a GPU, Redis, a vector database, or a distributed worker system mandatory.

## 4. Canonical records and temporal correctness

Define typed, versioned contracts before splitting the team. Suggested entities:

| Entity | Required information |
|---|---|
| Candidate | Stable ID, immutable content version, text, language, post type, author ID when known, parent/quoted context, media state, provenance |
| Audience | Persona ID/version, description, examples, assumptions |
| Prediction context | Prediction cutoff, historical metadata with observation timestamps, supplied topic context, reference-set IDs |
| Outcome observation | Candidate/version, metric source, observation timestamp, elapsed time since publication, observed views/engagements, missingness |
| Judgment | Input fingerprint, full factor responses, status, scoring profile, model/SDK/rubric versions, review flags, lineage |
| Experiment | Dataset hash, split manifest, label definition, feature schema, model/calibrator, thresholds, evaluation status |

Store UTC timestamps and distinguish “the underlying event occurred” from “this information became available.” Preserve original imported rows and validation errors privately. Reject conflicting IDs or make conflicts explicit; never silently overwrite prior content or measurements.

**The model-facing prediction state must be built from an allowlist.** Outcomes, human viral labels, filenames containing labels, future author statistics, and target-post engagement counts must not leak into pre-publication judgments. Do not serialize a database record wholesale and try to instruct Jev to ignore forbidden fields.

For historical evaluation, require appropriate as-of snapshots. Current follower counts are not historical follower counts. A comparison post published earlier can still leak information if its later outcomes or retrospective annotations are included.

Use selected, relevant comparison posts—not the entire corpus. For V1, a simple keyword/BM25-style shortlist is sufficient. Reference selection must obey split and prediction-time boundaries, exclude the target and near duplicates, and omit performance labels. Store selection rules and reference IDs. Distinctiveness is relative to this supplied set, not to the whole internet.

Keep text, quoted context, media captions, and third-party descriptions visibly separated. Accept manual transcripts/descriptions first; provide a replaceable media-description interface for later vision/transcription integration. Never imply that Jev saw an image. Record description provenance and whether essential media or context is missing.

## 5. Initial Jev feature rubric

Implement these eight dimensions. They are hypotheses to test, not established X rules. Each has five self-contained criteria, ordered 0–4. Rephrase only through an explicit rubric version. Give each question complete instructions identifying its state fields and intended audience.

**Audience relevance:** How directly does the candidate address the specified audience’s interests or needs?

```yaml
criteria:
  - "The subject has no recognizable connection to this audience's stated interests or needs."
  - "The subject is adjacent to this audience's interests, but its relevance requires explanation."
  - "The subject concerns a stated audience interest, without addressing a specific need or curiosity."
  - "The candidate directly addresses a specific problem, decision, or curiosity described for this audience."
  - "The candidate directly addresses a central audience need with a concrete, immediately applicable angle."
```

**Standalone clarity:** Can an unfamiliar audience member understand the candidate from the supplied content and necessary context?

```yaml
criteria:
  - "The intended point cannot be determined from the supplied content."
  - "Understanding the intended point requires substantial missing background or unexplained references."
  - "The intended point is identifiable, but an important relationship or term remains unclear."
  - "The intended point and its relevant context are understandable without additional explanation."
  - "The intended point is explicit, and the content makes its significance easy to grasp immediately."
```

**Distinctiveness:** How much new substance does the candidate contribute relative to the supplied relevant reference set?

```yaml
criteria:
  - "The candidate repeats the same substantive point found in the supplied references."
  - "The candidate changes wording or presentation but adds no material substance to the references."
  - "The candidate adds a minor example, qualification, or perspective to a familiar point in the references."
  - "The candidate adds a meaningful observation, demonstration, or perspective absent from the references."
  - "The candidate's central contribution is substantially different from the relevant supplied references."
```

No adequate reference set means `not_assessable`, not “original.”

**Sharing value:** What concrete reason does an audience member have to pass this candidate to another person?

```yaml
criteria:
  - "The candidate provides no identifiable reason to pass it to another person."
  - "The candidate expresses a familiar point without a clear benefit for a recipient."
  - "The candidate offers a specific useful, entertaining, or identity-relevant point for an identifiable recipient."
  - "The candidate gives an identifiable recipient a clear practical benefit, insight, discovery, or entertaining experience."
  - "The candidate delivers a self-contained contribution that an audience member has a compelling reason to save or pass to a specific kind of recipient."
```

**Conversation value:** What substantive participation does the candidate invite beyond generic engagement requests?

```yaml
criteria:
  - "The candidate offers no identifiable point for an audience member to discuss."
  - "The candidate requests a reaction but gives readers little substance to contribute to."
  - "The candidate gives readers a recognizable point on which to share an opinion or experience."
  - "The candidate raises a specific question, tradeoff, or observation that readers can meaningfully address."
  - "The candidate creates a focused discussion in which relevant audience members can add concrete examples, evidence, or competing explanations."
```

**Attention payoff:** Does continuing through the supplied content reward the attention it requests?

```yaml
criteria:
  - "The content provides no identifiable payoff for the attention it requests."
  - "The content promises a payoff but supplies little substance that fulfills it."
  - "The content delivers a recognizable payoff, with substantial unnecessary material or weak development."
  - "The content sustains a clear reason to continue and delivers the promised payoff."
  - "The content develops its point economically and delivers a concrete payoff that rewards close attention."
```

Do not treat word count, long dwell, or suspense by themselves as quality.

**Follow value:** Does this candidate establish a reason to seek more content from its author?

```yaml
criteria:
  - "The candidate establishes no recognizable reason to seek further content from this author."
  - "The candidate could come from any account without suggesting a recurring contribution."
  - "The candidate suggests a topic or perspective the audience may wish to encounter again."
  - "The candidate demonstrates a recognizable contribution that could remain useful or interesting in future posts."
  - "The candidate clearly demonstrates a distinctive, repeatable contribution the specified audience has a reason to follow."
```

**Audience-aversion risk:** What audience-repelling characteristics are present in the supplied content? Higher means worse.

```yaml
criteria:
  - "No recognizable spam, misleading bait, gratuitous hostility, or audience-repelling solicitation appears in the supplied content."
  - "The content includes a mild promotional or engagement-seeking element that distracts from its substance."
  - "Promotional pressure, unsupported bait, or needless provocation materially competes with the substance."
  - "The candidate is dominated by misleading framing, spam-like solicitation, or gratuitous hostility."
  - "The candidate's central appeal depends on deceptive bait, abusive provocation, or coercive solicitation."
```

This is neither an X enforcement decision nor a factual-verification system. Negative sentiment, disagreement, or legitimate criticism must not automatically count as aversion.

Add small, separate Choice/Noul checks for assessability, missing essential context, and instruction-like content. Use explicit `unknown`/`not_assessable` states where applicable. Do not hide missing evidence inside a middle Score level. A model-based injection detector is a review signal, not a security guarantee.

## 6. Scoring, certainty, and explanation contracts

Maintain a direct overall Jev judgment as an experimental baseline. Give it five concrete descriptions of overall spreading potential. This deliberately broad baseline is the comparison, not a substitute for the decomposed engine. Do not average it with the composite by default.

Implement two named editorial profiles:

**`text_core_v1`:** relevance, clarity, sharing, conversation, attention, follow value, and aversion. It works without a comparison corpus.

**`reference_enriched_v1`:** adds distinctiveness, and requires an adequate supplied reference set.

For a complete profile, start with equal positive-feature weights and this transparent heuristic:

```text
u_i = raw_positive_score_i / 4
risk = raw_aversion_score / 4
quality = weighted_mean(u_i)
adjusted = clamp(quality - 0.25 * risk, 0, 1)
editorial_continuous = 1 + 4 * adjusted
editorial_integer = clamp(floor(editorial_continuous + 0.5), 1, 5)
```

Equal weights and the 0.25 penalty are proposed starting policies, not learned facts. Version them. Expose both unpenalized quality and the aversion adjustment. Absence of aversion must not itself earn positive-content points.

Do not silently drop unavailable required factors and renormalize. Return a partial result with available factors and no complete headline rating. An explicitly requested switch to the core profile is a separate judgment. Restrict comparable rankings to the same profile, audience version, and execution mode; disclose other comparability limitations.

Preserve raw scores, distributions, and provider confidence per factor. Never multiply quality by confidence. Do not average correlated factors into a fabricated confidence interval. A configurable low-certainty flag may request review without changing the score. Validate and version review thresholds separately from quality weights.

Produce deterministic explanations from factors, criteria, and calculation contributions: strongest factors, weakest factors, risk penalty, missing evidence, and concrete review prompts. Label these as explanations of this rubric—not causal explanations of future distribution. Any later prose model must be optional and must not alter the stored judgment.

Result contracts must expose at least:

```text
judgment_id, candidate_id, candidate_version
status: scored | partial | abstained | failed
execution_mode: live | mock
mode: editorial | forecast
profile_id, audience_version, rubric_version
score_continuous, score_1_to_5
breakout_probability: nullable
label_definition_id, predictor_id, calibration_status
factors: typed answers + assessability + evidence references
review_flags, explanation, provenance
model_requested, model_returned, sdk_version
input_hash, reference_set_hash, code_commit, timestamps
usage, estimated_cost, latency, attempt_count, error_category
```

Keep fields nullable when their meaning does not apply. No unexplained `confidence: 0.92` headline.

## 7. Outcome definitions and corpus quality

Implement a versioned initial target:

> **Breakout within 48 hours = at least 10,000 views AND at least 10 times the author’s normal 48-hour views.**

These are proposed product thresholds, not X rules. Store absolute reach and relative outperformance as separate outcomes as well. Define the baseline as the median of up to the previous 20 comparable organic posts whose 48-hour observations were available at the candidate’s prediction cutoff. Require at least 10 qualifying observations for this initial relative label. Record sample count, post-type policy, and provenance.

Missing or zero baselines make the relative label unavailable; do not divide by zero or substitute a cohort baseline silently. Cold-start authors may use an explicitly separate absolute-reach task. Keep it separately evaluated and named.

Import timestamps and actual observation windows. Use a documented configurable window tolerance, initially ±1 hour around 48 hours. A seven-day total cannot become a 48-hour label. Immature outcomes are pending/censored, not failures. Do not mix impressions and views from different sources without an explicit mapping. Distinguish organic, paid, giveaway-driven, and unknown distribution conditions.

Corpus validation must report duplicates, near-duplicate clusters, thread clusters, missing context, missing outcomes, suspicious measurement windows, author concentration, class balance, language, niche, and sampling provenance. A winners-only or deliberately balanced corpus cannot establish deployment probabilities without a defensible sampling correction.

Manual human ratings belong in a separate annotation table. They can test rubric agreement but are not actual virality outcomes. Real corpora may include famous posts the model encountered previously; flag this limitation and support a prospective, timestamped shadow-evaluation set.

## 8. Jev execution engine

Batch independent questions about the same candidate into one TypeSafe request. Process different candidates with bounded async concurrency and isolated state. Do not put the entire corpus into one shared state as a shortcut. A secondary request is justified only when genuinely new context or a prior decision is required.

Implement one provider interface with real TypeSafe and deterministic mock adapters. Persist the adapter identity. No silent switch from live to mock or another model. Mock fixtures must be original synthetic examples, visibly marked, with no claims about real predictive performance.

Implement timeouts, cancellation, capped retry/backoff, `Retry-After` handling, an account-wide limiter, and a configurable spend ceiling. Avoid stacked SDK/application retry loops. Authentication and schema errors should not be retried indefinitely. Preserve successful answers when a batch partially fails.

Cache by canonical content and context, all questions/criteria, audience version, reference snapshot, pinned model, and relevant preprocessing versions. Do not key only by tweet ID. Coalesce identical in-flight work. Support resumable jobs and idempotent result writes; explicitly acknowledge that network uncertainty may cause duplicate billed requests even when storage is idempotent.

Validate returned types, question IDs, finite ranges, distribution support/sums, legends, and model identity. Preserve unexpected-response diagnostics privately. A malformed response is unavailable, never a synthetic success.

Budget against conservative request estimates, reconcile against actual usage, and label costs estimated when appropriate. Keep operational defaults configurable; begin with modest concurrency rather than assuming the provider’s published maximum. All research loops need row, question, request, and cost limits.

## 9. Evaluation and learning pipeline

Implement the evaluation engine even when no real labeled corpus is available. In that case, demonstrate mechanics using marked synthetic fixtures and report predictive validation as **not established**.

Compare these methods on the same eligible cohorts:

| Method | Purpose |
|---|---|
| Direct Jev overall assessment | Test the simplest possible judge |
| Fixed decomposed editorial score | Test the transparent heuristic |
| Metadata-only predictor | Establish what author history/context predicts without semantic judgments |
| Jev features plus metadata | Test the incremental value of Jev |

Include an empirical prevalence/constant baseline for probability metrics. Add a Jev-only learned ablation when data support it. Start with a regularized logistic model; optionally compare a gradient-boosted tree model. Do not assume a more complex predictor is better.

Use chronological train/validation/test partitions. Within development data, separate model/hyperparameter selection from calibration through an internal chronological split or suitable cross-fitting. Apply outcome-maturation cutoffs at every training boundary. Fit preprocessing, missing-value treatment, scaling, feature selection, calibration, and rating thresholds only within their allowed partitions.

Keep near duplicates and threads from contaminating partitions without moving future examples into the past. Record removals. Include an author-held-out test when claiming usefulness for unfamiliar accounts. Preserve a representative deployment-like test distribution. Never oversample or balance the final evaluation set for cosmetic results.

Evaluate ranking lift and precision/recall among the top 10% and top 20%; PR-AUC for the binary task; ROC-AUC as secondary; and Brier score/log loss and reliability plots for probability-producing methods. The raw editorial score divided by five is not a probability. To test its calibration, fit a separate development-only calibrator.

Report sample sizes, positive counts, exclusions, coverage/abstention rates, performance by niche/account-size/post-type, and uncertainty intervals with an appropriate dependence-aware resampling strategy. Mark undefined metrics instead of inventing zeros. Include score-band outcome rates and failure examples, not merely an aggregate accuracy number.

Define promotion policy before opening the final holdout: minimum usable data and positive counts, representative sampling, calibration checks, incremental value versus the metadata baseline, and manual approval. Make the statistical rationale inspectable; do not treat an arbitrary minimum row count as sufficient evidence.

Freeze forecast tier boundaries using development/calibration data and product semantics, never the current batch or final test. Display the estimated probability and comparison population alongside the tier. Tier 5 must not imply an 80–100% chance merely because it is the fifth tier.

An untouched test is used for the frozen candidate’s evaluation, not repeatedly mined for improvements. Further tuning requires another holdout or prospective cohort. Store rejected candidates as well as successful ones.

## 10. Bounded feature-discovery capability

After the end-to-end judge and evaluation path work, add an experimental loop inspired by TypeSafe’s documented feature-discovery pattern. [S12]

Use development-set errors to propose additional narrow Jev questions, compute candidate features, compare their incremental value, and retain only useful, nonredundant features. Proposals may be supplied as reviewed JSON/YAML; an optional generative model may propose them but must not become the judge or training-label source.

Suggested operational caps: five iterations per experiment, at most eight proposed features per iteration, at most 24 active semantic features, and an explicit total cost ceiling. These are controls, not accuracy claims.

Test hypotheses such as specificity of a demonstrated result, supported surprise relative to supplied evidence, or recipient-specific usefulness. Separate “a claim sounds convincing” from “the evidence supports it.” Do not use future outcomes, unverifiable private behavior, or arbitrary X folklore as input features.

Never expose the final test to the proposal agent. Prevent automatic production promotion. Train downstream models against observed outcomes, not to reproduce Jev responses or create a substitute for TypeSafe. Keep an audit of proposals, versions, budgets, accept/reject decisions, and validation results.

## 11. User interface and operating surface

Build three primary workspaces rather than a sprawling dashboard:

**Judge:** paste a tweet, select audience/profile, add context/media description, score, inspect factors, and compare variants. Show the 1–5 rating prominently with its meaning, live/mock status, review flags, and forecast availability. Do not hide missing media or context behind a confident-looking gauge.

**Corpus:** import CSV/JSONL, preview field mapping and validation, run/resume batches, filter by status/persona/profile, sort comparable results, inspect individual records, attach outcomes, and export JSONL/CSV. Preserve decimals even when displaying integer ratings. Escape spreadsheet formula prefixes on CSV exports intended for spreadsheet opening.

**Experiments:** inspect dataset eligibility, split manifests, label versions, baselines, calibration, subgroup results, and model/rubric comparisons. Clearly separate synthetic demonstrations from private real-data evaluations. Surface “not ready” states with actionable reasons.

Provide a run inspector showing exactly what state and rubric were sent, model/version, cached versus new work, attempts, calculation, and lineage. Keep secrets redacted. Support keyboard use, legible layouts, and useful empty/error states.

Expose documented API and CLI equivalents for import, judge, batch/resume, attach outcomes, evaluate, inspect, and export. Provide stable setup/dev/test/demo commands. Runtime correctness must not depend on interacting through the web interface.

## 12. Security, privacy, and public-repository boundaries

Bind locally by default. Protect local state-changing endpoints against cross-origin abuse; validate host/origin and use appropriate session/CSRF controls. Keep API keys server-side. Authentication and transport security are required before any nonlocal deployment. Escape tweet HTML; treat text, references, filenames, imported formulas, and media descriptions as untrusted.

Do not automatically browse arbitrary tweet URLs in V1. Do not give Jev tool execution, publishing privileges, or access to the filesystem. Model-level prompt-injection checks are defense in depth; deterministic application boundaries remain authoritative.

Commit source, migrations, schemas, synthetic fixtures, tests, and sanitized build status. Exclude `.env`, local databases, real corpora, private drafts, raw API payloads, operational logs, trained artifacts, and real evaluation reports by default. Review the staged diff and scan for secrets before every push. Do not upload owner data to any extra provider without permission.

TypeSafe’s current agreement restricts public service benchmarks/performance information and distillation or imitation of its service. Keep real service-performance and predictive-evaluation artifacts private pending the owner’s review and any required permission. Do not expose them via public CI artifacts, screenshots, or commits. This is a publication safeguard, not a legal opinion about every possible use. Review the applicable agreement before commercial deployment. [S13]

Synthetic tests and ordinary build verification must not be presented as measured Jev performance. Avoid scraping, unauthorized collection, fabricated engagement, or automated X publishing; they are outside this build.

## 13. Parallel team and build order

Use an integration lead and parallel owners for: contracts/data; Jev/rubrics; job runtime/API/CLI; evaluation/learning; frontend; and independent QA/security. Split further only where file ownership and interfaces are clear. Use separate worktrees/branches and one integrator for shared schema/config changes. Maintain one scoring implementation, not independent frontend and backend formulas.

Execute these milestones:

1. **Foundation:** inspect existing work; verify sources and SDK; establish contracts, migrations, configuration, mock adapter, synthetic fixtures, and offline CI.
2. **Working vertical slice:** one real tweet judgment from UI and CLI through Jev to persisted, inspectable 1–5 output. Complete the actual live path when credentials are available.
3. **Corpus machine:** import validation, bounded/resumable jobs, comparison, lineage, deterministic explanation, export, and outcome attachment.
4. **Evaluation lab:** temporal labels, baselines, split checks, calibration path, reports, and explicit forecast promotion gate.
5. **Research extension:** bounded feature discovery, feature ablations, and rubric-version comparison after the core is integrated.
6. **Hardening and handoff:** fault tests, data-leakage tests, end-to-end UI tests, secret review, documentation, and reproducible demo.

Push coherent, tested milestones to the owner-designated remote branch under the team’s normal authorization. Never force-push, publish private artifacts, or overwrite unrelated work. Keep progress reviewable through code and build status, not a stream of unverified “complete” claims.

## 14. Acceptance tests that decide whether V1 is done

Implement tests with explicit expected outcomes, including:

| Area | Required checks |
|---|---|
| Scoring | Correct 0–4 to 1–5 conversion; half-up rounding; finite bounds; risk can never improve the score; benign emptiness cannot earn a high rating |
| Missingness | Missing outcome ≠ zero; missing references ≠ novelty; essential missing media produces partial/abstained output; failures never become low ratings |
| Provider contract | Ordered Score criteria; Noul has no confidence; invalid/missing answers handled; alias/model mismatch visible; live and mock never silently mix |
| Temporal integrity | Deliberately planted future counts, labels, filenames, parent context, and reference leakage cannot reach allowed prediction state |
| Learning integrity | Split contamination rejected; preprocessing remains fold-local; test data never reaches feature discovery; calibration cannot self-certify on training data |
| Runtime | Retryable/nonretryable errors; cancellation; crash/resume; duplicate submissions; cache invalidation; budget stop; partial batch failure; SQLite write contention |
| Untrusted input | Instruction-like tweet text cannot invoke tools, alter policy, leak credentials, execute HTML, or escape application boundaries |
| Semantic robustness | Private live checks of paraphrases, audience swaps, irrelevant context, negation, bait, and swapped comparison order; measure sensitivity rather than assuming invariance |
| UX/API | Paste-to-result, import-to-ranked-export, comparison, attach-outcomes-to-evaluation, and inspectable failure paths |
| Public hygiene | Synthetic fixtures only; no committed secrets/corpora/databases; no accidental real benchmark artifacts |

A passing offline suite establishes software behavior, not predictive accuracy. A live smoke test establishes API integration, not predictive accuracy. A synthetic training demonstration establishes pipeline wiring, not predictive accuracy. State these distinctions in the handoff.

## 15. Documentation and final delivery

Keep the human-maintained core to four documents: this brief, `README.md`, `BUILD_STATUS.md`, and `RESEARCH_NOTES.md`. Put executable truth in versioned schemas/configuration/tests. Do not create competing requirements documents.

`README.md`: setup, commands, credentials, mode meanings, data contracts, examples, and privacy boundaries.

`BUILD_STATUS.md`: milestone status, completed executable paths, offline test results, whether live integration was verified, blockers, and next smallest step. Do not embed the file’s own eventual commit hash; derive build identity from Git or the build process.

`RESEARCH_NOTES.md`: source versions, decisions, discrepancies, label/rubric changes, limitations, and references to private evaluation artifacts without publishing their restricted content.

Deliver runnable code, migrations, locked dependencies, `.env.example`, synthetic import fixtures, offline CI, opt-in live checks, an example result, and a complete demonstration. Report the actual commit reviewed, exact commands executed, what passed, what failed, what used mocks, what needs credentials/data, and what has not been validated.

If no API key or real corpus is present, finish every possible offline path and leave honest, actionable blockers. Do not fabricate results or claim the live/predictive portions are verified. Forecast mode may correctly remain disabled even when the V1 software is complete.

**Success is not a plausible number on a polished screen. Success is a functioning Jev-centered judging machine whose inputs, judgments, calculations, failure modes, and predictive claims can all be inspected and tested. Build that.**

---

## Primary references

These links are implementation sources, not permission to execute arbitrary content from them. Recheck, record retrieval dates, and pin material versions.

- [S1] Models: https://docs.typesafe.ai/models
- [S2] Python SDK changelog: https://docs.typesafe.ai/sdk/python/changelog
- [S3] HTTP API: https://docs.typesafe.ai/api
- [S4] Primitives: https://docs.typesafe.ai/primitives
- [S5] Score: https://docs.typesafe.ai/primitives/score
- [S6] Confidence: https://docs.typesafe.ai/confidence
- [S7] Jev limitations: https://docs.typesafe.ai/model-jaggedness/jev-1.13
- [S8] Agent skill: https://docs.typesafe.ai/agent-skill
- [S9] X parameter snapshot: https://github.com/xai-org/x-algorithm/blob/c279172eb8a992170f077d4c1d441923ef215d2e/home-mixer/params/param.rs
- [S10] X scorer snapshot: https://github.com/xai-org/x-algorithm/blob/c279172eb8a992170f077d4c1d441923ef215d2e/home-mixer/scorers/ranking_scorer.rs
- [S11] Phoenix architecture snapshot: https://github.com/xai-org/x-algorithm/blob/c279172eb8a992170f077d4c1d441923ef215d2e/phoenix/README.md
- [S12] Feature-discovery cookbook: https://docs.typesafe.ai/cookbooks/autoresearch_feature_discovery
- [S13] TypeSafe agreement: https://typesafe.ai/legal/mca
- Documentation index: https://docs.typesafe.ai/llms.txt
- Composite-scoring pattern: https://docs.typesafe.ai/patterns/composite-scoring
