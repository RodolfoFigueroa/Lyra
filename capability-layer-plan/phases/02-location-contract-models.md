# Phase 02 — Location contract models

Read the [shared workflow](index.md). Continue after
[phase 01](01-performance-baseline.md). Apply ledger decisions **2, 12, 13, 19, 20**.

Establish reusable location families and concrete contracts without conflating
geographic identity with the observations attached to a unit.

## Work

- Define a family-level structural schema covering columns, types, nullability,
  structural constraints, ID and geometry conventions, and units. Reuse existing
  schema and immutable location machinery where appropriate.
- Represent concrete source and geographical edition bindings separately from the
  shared schema. Preserve source meaning, observation period, release, and field
  definitions as metadata; several observation periods may share one geography.
- Define unit identity using collection, unit type, geographical edition, and ID.
  Keep concrete source-contract identity separate, even when two contracts describe
  the same geographic units.
- Allow an optional data revision on each concrete location definition, including
  implicit editions. Preserve absence as unversioned rather than inventing a
  revision. Keep revisions distinct from edition and unit identity.
- Establish reliable equality and serialization for these contracts and their
  references, making the minimum adjustments needed by current consumers.

## Acceptance and validation

- Test equal IDs across different geographies, different observations over one
  geography, and distinct contracts sharing the same revision string.
- Test shared schema reuse and structural constraints, including metadata round
  trips and omitted revisions.
- Confirm that changing a revision does not create a different geographic unit
  or a new user-selectable edition.
- Run the index's Python checks and affected location/schema tests.

## Boundary

This phase establishes declaration models and identity rules. Runtime location
values and artifacts are integrated in the next phase; provider lookup, revision
pinning, and invocation invalidation follow later. Do not add a separate source
abstraction, correspondence inference, schema reconciliation, or harmonization.
