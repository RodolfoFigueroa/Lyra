# Phase 14 — Accepted source selections

Read the [shared workflow](index.md). Continue after
[phase 13](13-route-preferences.md). Apply ledger decisions **3, 4, 10, 15, 24, 25**.

Compile the source collection/unit-type pairs each named metric input accepts,
with an unambiguous concrete binding for the selected metric edition.

## Work

- For every concrete metric variant and named location input, project compatible
  canonical routes onto its accepted public source types. Include the native
  contract's direct zero-hop selection.
- Resolve each accepted collection/unit-type pair to exactly one concrete source
  contract for that metric variant and input. Validate the declarations needed to
  establish this binding; reachability alone must not choose between editions.
- Reject ambiguous bindings during catalog compilation. Do not guess a latest
  source edition or ask callers to repeat edition selection inside each location
  selection.
- Preserve edition-specific availability, native input meaning, and output
  granularity. A coarse source selection may still produce fine-grained metric
  rows.
- Keep accepted selections structured and derived from declarations and routes.
  Preserve operation descriptions as relationship metadata without generating
  per-route prose or inferring containment, equivalence, grouping, or aggregation.

## Acceptance and validation

- Test native-only inputs, routes contributed by another plugin, multiple named
  inputs with different accepted types, and edition-specific reachability.
- Reject two reachable source contracts that cannot be distinguished by the
  variant's declared public selection binding.
- Verify private variants remain internally usable without becoming publicly
  selectable through this projection.
- Run the index's Python checks and affected catalog/schema tests. Measure a
  catalog checkpoint including accepted-source compilation and edition families.

## Boundary

Do not perform acquisition or build public discovery endpoints. This phase provides
the authoritative contracts used by request preparation and later inspection.
