# Phase 20 — Revision-aware invocation caching

Read the [shared workflow](index.md). Continue after
[phase 19](19-dependency-binding-semantics.md). Apply ledger decisions **18–23**.

Integrate compiled revision identities with the existing native invocation cache
for metrics and location operations.

## Work

- Key concrete invocations on effective parameters, complete ordered native
  contents and contracts/editions, relevant native and calculation-dependency
  revisions, structural computation identity, and optional run-wide revision.
- Use the compiled revision closures before cache lookup, including the target
  dataset revision of a location-producing operation. A native revision change
  invalidates the invocation even if its values remain equal.
- Scope revisions to concrete contracts and preserve omitted revisions as
  unversioned. Keep content-based caching and catalog-wide structural namespaces.
- Let a new run-wide revision refresh all invoked nodes without deleting old
  entries; subsequent runs with that value may reuse them.
- Keep cached native artifacts independent of request-specific provenance. Use
  resolved native invocations and explicit dependency graphs to establish the
  cache contract before completing routed-selection reuse in the next phase.

## Acceptance and validation

- Test equal IDs with different geometry, attributes, contracts, editions, or
  ordered contents.
- Cover native, transitive calculation, target-membership, unrelated, and omitted
  revision changes, including unchanged contents with a changed native revision.
- Verify shared dependency reuse, revision scoping, run-wide namespace reuse, and
  retention of older cache entries with temporary files and fake resources.
- Run the index's Python checks and affected cache/execution tests. Measure
  representative cold, warm, and revision-invalidation workloads.

## Boundary

This phase establishes correct native invocation keys. The next phase owns their
composition with public canonical selection. Do not expose new routed cache reuse
until that integration is validated, or add per-ID, subset, or code-version caches.
