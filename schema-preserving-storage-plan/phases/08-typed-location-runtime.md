# Phase 08 — Typed location runtime

After phase 07, read the [index](index.md) and
[decision ledger](../decision_ledger.md). Connect the prepared storage primitives
to Locations and replace the tagged-property runtime.

## Work

- Construct location snapshots from the declared typed columns, using the shared
  conversion, ownership, and fingerprint implementations. Preserve the existing
  geometry buffers and ingest producer geometry once.
- Remove tagged scalar properties, per-row property absence, `MISSING`, and the
  corresponding public exports and obsolete scalar reconstruction paths. Every
  row has the declared columns, with one logical null subject to nullability.
- Connect lazy IDs/properties, metadata access, slicing, and ID selection to the
  typed snapshot. Preserve established selection ordering and errors. Subsets
  retain the type contract without rerunning arbitrary producer checks.
- Reconstruct DataFrames/GeoDataFrames with declared dtypes, normalized indexes,
  structural column roles, and CRS. Explicit projections follow requested column
  order; nonspatial projections do not decode geometry.
- Update direct readers and exporters affected by the representation change.
  Route existing location artifact read/write entry points through the phase 06
  codec now, so cache and standalone callers continue to work. Phase 12 examines
  those paths in depth; basic persistence correctness cannot wait until then.

## Acceptance

- Fresh values, empty/all-null outputs, selections, and frame exports retain
  declared types and metadata. No generic object export is used to hide lost
  typing. Supported temporal and numeric boundary fixtures remain exact.
- Property access, length, IDs, and metadata stay lazy. Selecting property
  columns does not materialize unrelated properties or geometry.
- A subset can violate a producer's whole-table check while remaining a usable
  typed subset. Returning it from an operation still invokes that operation's
  output validation.
- Producer/export mutation cannot affect snapshots or cached fingerprints.
  Equivalent subsets retain the expected content identity.
- Existing location artifacts, caches, and explicit exports use the new runtime
  successfully. Old-format artifacts are rejected, not translated.

## Verification and boundary

Follow shared verification, cover the runtime and its direct artifact/export
callers, and run the full suite. Use already tested foundations instead of adding
another codec or conversion layer. Leave export edge cases, validation reuse,
and broader cache integration to their dedicated phases.
