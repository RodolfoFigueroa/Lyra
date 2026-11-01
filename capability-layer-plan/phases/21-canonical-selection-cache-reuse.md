# Phase 21 — Canonical selection cache reuse

Read the [shared workflow](index.md). Continue after
[phase 20](20-revision-aware-invocation-caching.md). Apply ledger decisions **8, 18, 19, 22, 27**.

Compose canonical selection with revision-aware native caching without making
selection-only data revisions part of the metric's calculation identity.

## Work

- Execute or validly reuse each bound selector before metric cache lookup.
  Selectors use the same cache and resource scope as explicit location dependencies.
- At the resolved native-input boundary, use the metric's native contracts,
  ordered contents, and calculation revision closure. Exclude revisions used
  solely to reach those inputs; retain any also required by the calculation.
- Allow different original source selections and routes to reuse a metric result
  when all effective native identity components agree. Equal IDs alone are
  insufficient.
- Keep the accepted route fixed regardless of cache warmth or failures. A metric
  hit may still require source acquisition and intermediate artifact reads.
- Ensure cached artifacts do not carry an earlier run's selection or route as the
  current provenance. Detailed execution records are completed in
  [execution provenance](23-execution-provenance.md).

## Acceptance and validation

- Reach equivalent native inputs through distinct source selections and routes;
  verify metric reuse while observing the required selector work.
- Change only a selection-source revision, then native, target, and explicit
  calculation revisions. Check the intended reuse/invalidation at each boundary.
- Cover a contract used by both selection and calculation, warm selector hits,
  unchanged native contents with a changed native revision, and failed fixed routes.
- Run the index's Python checks and relevant routing/cache tests. Measure routed
  cold/warm and selective-invalidation cases against comparable checkpoints.

## Boundary

Do not bypass input resolution or promise zero-I/O, permutation-insensitive,
per-ID, or subset reuse. Retain the native key semantics established previously.
