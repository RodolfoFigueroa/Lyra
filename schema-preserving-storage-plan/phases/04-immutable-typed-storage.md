# Phase 04 — Immutable typed storage primitives

After phase 03, read the [index](index.md) and
[decision ledger](../decision_ledger.md). Establish ownership and type-preserving
operations before connecting the new representation to public runtime values.

## Work

- Build private owned storage using the shared conversion contract. Retain the
  information needed to reconstruct declared frame dtypes, including for empty
  tables, without retaining mutable producer frames or executable schema checks.
- For locations, combine typed identity/property columns with the existing
  geometry buffers and scope metadata. Preserve IDs, collection, unit type,
  structural column roles/order, and CRS. Keep geometry encoding unchanged.
- Detach caller-owned mutable memory at the ownership boundary. A frozen Python
  object alone is insufficient if exposed Arrow buffers remain writable.
- Support internal slicing, row selection, and column projection while retaining
  type contracts and safe ownership. Reuse immutable backing where possible;
  newly allocated selection buffers must also be safe to expose.
- Separate validation of arbitrary incoming buffers from derivation of subsets
  of an already valid snapshot. Subsets must not rerun producer Pandera checks.

## Acceptance

- Mutating input frames, arrays, or mutable source buffers after construction
  cannot change stored values. Exported mutable data cannot change the snapshot.
- Accessible buffers cannot be used to mutate snapshot contents. Malformed
  structural metadata and mismatched geometry/property row counts are rejected.
- Empty and nonempty slices/selections retain dtypes, row order, and spatial
  metadata. Explicit projections retain requested column order.
- Structural access and nonspatial projections do not decode geometry or build
  feature dictionaries. Geometry is ingested once when constructing from a frame.

## Verification and boundary

Follow shared verification and run focused ownership, buffer-validation, and
selection tests. These primitives may remain private and unconnected to the
existing runtime until phase 08. Do not create a new public metric-result wrapper
or add placeholder fingerprint values; identity is implemented next.
