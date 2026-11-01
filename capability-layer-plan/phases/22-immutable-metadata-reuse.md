# Phase 22 — Immutable metadata reuse

Read the [shared workflow](index.md). Continue after
[phase 21](21-canonical-selection-cache-reuse.md). Apply ledger decisions **18, 19, 23**.

Remove repeated work from the completed preparation and cache paths without
changing identity or invalidation semantics.

## Work

- Inspect the compiled catalog, revision plan, preparation, and invocation paths
  for repeated graph serialization, package-version lookup, namespace hashing, or
  dependency-revision derivation.
- Reuse immutable computation namespaces and dependency identities for fixed
  catalog, provider, and revision bindings. Preserve run-wide revision isolation
  without rehashing the entire graph on every node lookup.
- Reuse immutable location snapshots and their content fingerprints within a run,
  including shared dependency and selector results. Avoid copying or encoding
  unchanged complete location contents just to obtain an existing identity.
- Keep reuse scoped to valid lifetimes. A new catalog or changed binding must not
  inherit stale metadata from another run or configuration.
- Adapt relevant benchmarks to the completed paths while preserving workload and
  cache-state comparability where possible.

## Acceptance and validation

- Use counters or spies around expensive derivation boundaries to show repeated
  invocations reuse fixed metadata and shared snapshots. Do not assert wall-clock
  timing in correctness tests.
- Verify different catalogs, revisions, providers, and run-wide revisions retain
  the correct independent identities.
- Recheck the invalidation cases affected by the optimization; unchanged data
  values must not erase a declared native revision difference.
- Run the index's Python checks and relevant identity/cache tests. Measure
  representative chains, shared dependencies, and larger batches; inspect memory
  as well as runtime against baseline and previous comparable results.

## Boundary

Do not introduce request-level shortcuts that bypass resolution, incremental
location caching, or narrower structural invalidation. Optimize established
immutable boundaries rather than inventing another cache system.
