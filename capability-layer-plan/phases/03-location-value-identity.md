# Phase 03 — Location value identity

Read the [shared workflow](index.md). Continue after
[phase 02](02-location-contract-models.md). Apply ledger decisions **2, 12, 18, 23**.

Carry the location contracts from phase 02 through actual resolved values before
changing provider registration.

## Work

- Extend the existing immutable location representation to retain concrete source
  and geographical edition identity alongside complete geometry and attributes.
  Keep geographic unit identity distinct from source-contract identity.
- Preserve that identity through construction, slicing, ID selection, derived
  snapshots, and relevant exports. Reuse the existing ownership and schema
  validation machinery rather than creating another location container.
- Ensure content identity distinguishes otherwise equal rows from incompatible
  contracts or geographies. Keep revision bindings available without confusing
  them with geographic identity; dependency revision planning comes later.
- Update location artifact encoding/decoding so a cached or serialized batch does
  not lose its concrete contract. Make necessary constructor and fixture changes
  together, without old-format readers or compatibility shims.

## Acceptance and validation

- Construct equal IDs and row contents under different geographic editions and
  source contracts; confirm they cannot be substituted through location equality
  or content identity.
- Exercise slices, empty batches, ID selection, and artifact round trips. Preserve
  attributes, geometry, order, identity, and immutable ownership.
- Check that existing snapshot sharing and lazy fingerprint reuse still work.
  Use temporary artifacts and in-process values.
- Run the index's Python checks and affected location, fingerprint, and artifact
  tests.

## Boundary

This phase owns runtime value identity and persistence, not provider selection.
The next phase connects providers to these values. Invocation revision closures
and cache invalidation remain later work.
