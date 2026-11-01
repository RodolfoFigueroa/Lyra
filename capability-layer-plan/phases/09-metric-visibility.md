# Phase 09 — Metric visibility

Read the [shared workflow](index.md). Continue after
[phase 08](08-family-validation.md). Apply ledger decisions **11, 24, 28**.

Make metric visibility explicit while preserving private intermediates as normal
graph nodes.

## Work

- Make metrics public by default and support private concrete variants in
  declarations and manifests. Keep the flag specific to metrics; operation
  routing will use canonical status.
- Centralize the public-entry policy so existing discovery and direct public
  submission paths exclude private variants. A family with no public variants
  must not appear in ordinary discovery.
- Keep private nodes available to dependencies, caching, and relevant structural
  inspection. Privacy controls entry points rather than secrecy.
- Retain intentional local development/test access without turning it into an
  ordinary public execution route.
- Adapt existing public consumers sufficiently to enforce visibility now. The
  later family discovery and transport phases own their complete new projections.

## Acceptance and validation

- Test default visibility, explicit private declarations, public/private edition
  mixtures, and wholly private families.
- Reject direct public execution of a private variant through current public
  entry points while allowing a public metric to depend on it.
- Verify private dependency reuse and intentional development access, using fake
  providers and resources.
- Run the index's Python checks and affected declaration, catalog, entry-point,
  and execution tests.

## Boundary

Do not make private nodes inaccessible to the graph or introduce authorization
roles. Complete family discovery and shallow relationship views in their later
phases, reusing this policy.
