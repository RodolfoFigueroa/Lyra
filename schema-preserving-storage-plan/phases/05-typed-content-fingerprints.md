# Phase 05 — Typed content fingerprints

After phase 04, read the [index](index.md) and
[decision ledger](../decision_ledger.md). Give owned typed contents exact,
layout-independent identity.

## Work

- Implement a versioned SHA-256 content protocol for normalized typed tables and
  location snapshots. Include column names/order, types and relevant parameters,
  values, nulls, and row order. Include location IDs, geometry, collection, unit
  type, and CRS.
- Exclude pandas indexes, descriptive metadata, and validation functions. Use
  only identity-relevant metadata; hashing serialized artifact bytes would also
  capture irrelevant physical layout.
- Normalize Arrow chunking, slice offsets, and unused null payloads for hashing.
  Preserve exact scalar distinctions, signed zero, temporal parameters, and
  geometry coordinates. Do not introduce semantic-equivalence hashing.
- Cache digests only on owned immutable snapshots. Derived snapshots may compute
  their own identity when needed; never cache identity on mutable metric frames.
- Retain useful geometry/non-geometry identity components where existing
  callers require them, under the new protocol.

## Acceptance

- Changes to values, types, column order, row order, null placement, signed zero,
  geometry coordinates, and spatial scope affect identity as applicable.
- Identical contents hash identically across chunk boundaries, equivalent
  slices, copied storage, and different irrelevant null payload bytes.
- Empty/all-null typed columns remain part of identity. Empty tables with
  different declared types do not collapse into the same content identity.
- Index, prose, and executable-check changes do not change data identity.
  Mutation-isolation tests ensure a cached digest cannot become stale.
- Basic native categorical contents are handled without exhaustive custom
  category normalization or repair machinery.

## Verification and boundary

Follow shared verification and run focused fingerprint and ownership tests.
Document enough of the protocol near its implementation to maintain it. Do not
change computation cache-key policy or inspect source code for cache invalidation.
Keep existing runtime callers functional until the planned representation switch.
