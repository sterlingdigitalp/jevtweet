# Research notes

Retrieved 2026-09-18. The build brief remains the initial product specification.

## Verified contracts

- TypeSafe [models](https://docs.typesafe.ai/models): pinned `jev-1.13.0`, text only; 64k total / 32k state plus longest question; published input price $0.042/M tokens, output free. Operational limits will be conservative and configurable.
- [Python changelog](https://docs.typesafe.ai/sdk/python/changelog) and PyPI confirm `typesafe-sdk==0.7.0`; Pydantic serialization, ordered Score criteria (since 0.6). Python >=3.10; this project targets >=3.12.
- [API](https://docs.typesafe.ai/api): POST `/v1/systemone`; `state`, `model`, keyed `questions`; Score/Choice distributions and confidence; Noul has only probability. SDK `AsyncTypeSafeClient.system_one` is the adapter boundary. Application owns retries with SDK retries disabled.
- Material documentation inconsistency: HTTP examples return alias `jev-latest`, while models reference says response model reports a pinned version. Enforce requested identity, surface mismatches, never silently accept alias drift.
- [Limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13): arithmetic, time and boundaries stay in code; irrelevant/adversarial state and literal interpretation remain research risks.
- Official [agent skill](https://docs.typesafe.ai/agent-skill) read directly; no installer executed. Read installed TypeSafe skill and relevant live primitive, confidence, SDK, composite-scoring and feature-discovery references.
- [X parameters](https://github.com/xai-org/x-algorithm/blob/c279172eb8a992170f077d4c1d441923ef215d2e/home-mixer/params/param.rs), scorer, and Phoenix snapshot retrieved at brief commit. Base coefficients match brief. These multiply personalized predicted actions, not observed counts or Jev features. Additional signals/adjustments exist.
- [TypeSafe agreement](https://typesafe.ai/legal/mca) retrieved; real payloads, service-performance measurements and predictive reports stay private pending owner review. No service imitation/distillation; downstream models use observed outcomes.

## Initial decisions

Strict input allowlists and UTC availability timestamps; private SQLite with migration and immutable version identities; explicit mock/live; one backend scoring function; profiles and thresholds versioned together. Default seeded personas are hypotheses. Evaluation mechanics on synthetic data establish no predictive claim.
