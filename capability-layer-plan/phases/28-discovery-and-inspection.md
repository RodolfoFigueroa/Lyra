# Phase 28 — Discovery and inspection

Read the [shared workflow](index.md). Continue after
[phase 27](27-remote-acceptance-and-persistence.md). Apply ledger decisions **1, 11, 13, 15, 24, 25, 27**.

Build compact family discovery and detailed inspection from the authoritative
compiled catalog, without duplicating author declarations.

## Work

- Return one list/search entry per family with public variants. Include concise
  meaning, declared output granularity, and edition-selection information. Exclude
  private variants and families with no public variants from ordinary discovery.
- Index accepted source types while preserving edition-specific availability.
  Do not advertise that every edition accepts the union of all family sources.
- For named-edition family inspection, expose the shared native input, output,
  and complete ordinary parameter contracts with public edition choices. Inspecting
  a concrete edition supplies its exact executable bindings and accepted selections.
  Uneditioned inspection exposes the executable contract directly.
- Include schemas, units, definitions, source/release context, and structured
  accepted location selections at the appropriate inspection level.
- Keep list/search compact and dependency details separate. Preserve operation
  descriptions where relevant without generating metric/route descriptions or
  claiming path equivalence or statistical comparability.
- Adapt existing discovery fingerprints, ordering, pagination, and search helpers
  to family-level entries rather than duplicating entire edition graphs.

## Acceptance and validation

- Test implicit, single-named, and multiple-edition families; public/private
  mixtures; and wholly private families.
- Verify search matches accepted source types only for public editions that
  support them, and inspection agrees with what preparation accepts.
- Check complete parameter constraints and source/result field definitions survive
  projection. Verify compact responses omit execution-graph expansion.
- Run the index's Python checks and catalog/discovery tests. Measure family list,
  search, and inspection costs and response sizes across representative catalogs.

## Boundary

Implement shared discovery and inspection projections. Transport-specific wiring
follows next; operation search and route exploration remain out of scope.
