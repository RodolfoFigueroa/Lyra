# Phase 11 — Metric cache integration

After phase 10, read the [index](index.md) and
[decision ledger](../decision_ledger.md). Replace metric persistence while keeping
the native execution model.

## Work

- Store every named metric output through the shared uncompressed IPC codec.
  Supply the registered type contract at the storage boundary wherever it is
  otherwise unavailable; do not infer a declaration from result rows.
- Replace metric Parquet storage, categorical sidecars, and manual index/dtype
  restoration with the shared artifact contract. Update bundle metadata and
  protocol namespace/version as required, without old-format readers.
- Preserve complete multi-output publication, locking, atomic replacement,
  manifest/member validation, and graceful cache failure behavior. A partial or
  corrupt bundle must never become a partial successful invocation.
- Verify stored types and fingerprints on read, then validate all restored
  outputs against their current registered schemas before downstream use.
- Keep producer returns and dependency arguments as native frames. Perform
  storage conversion and hashing at persistence boundaries, not at every metric
  dependency edge. Do not retain cached fingerprints on mutable native frames.
- Preserve computation-key inputs: node identity, effective parameters, input
  location fingerprints, graph contracts, plugin versions, and cache revision.

## Acceptance

- Fresh and cache-hit executions preserve named outputs, declared dtypes, row and
  column order, normalized indexes, and spatial metadata across the fixture matrix.
- Downstream metrics receive native frames on both paths. In-memory dependency
  reuse does not trigger Arrow round trips or repeated content hashing.
- Corrupt payloads, incorrect fingerprints, missing members, wrong output names,
  and unsupported versions are handled as unusable cache entries. Failed
  publication cannot expose an incomplete bundle.
- A structurally valid stored result that violates current executable checks is
  rejected before reuse. Cache-hit tests prove the producer was skipped only for
  a valid reusable result.
- Relevant parameter, input-location, graph-contract, plugin-version, and revision
  changes retain conservative invalidation behavior.

## Verification and boundary

Follow shared verification, cover table-cache and graph-execution behavior with
temporary files and in-process plugins, and run the full suite. Do not add cache
backend selection, source inspection, fine-grained invalidation, or a public
metric-result wrapper. Native categorical limitations remain explicit.
