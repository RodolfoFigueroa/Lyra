# Phase 07 — Edition registration

Read the [shared workflow](index.md). Continue after
[phase 06](06-concrete-dependency-references.md). Apply ledger decisions **13, 14, 15**.

Support explicitly registered metric families and concrete edition graphs built
with ordinary Python factories.

## Work

- Register family association and concrete variant identity as distinct concepts.
  Factories may share handlers and graph-building code while producing different
  concrete dependencies and source/geography bindings.
- Normalize an uneditioned capability to one implicit variant without requiring a
  factory or public edition selector. Preserve whether editions were explicitly
  declared, independently of the number installed.
- Require explicit edition selection for a named-edition family, including one
  with a single edition. Do not choose a latest edition or pass edition selection
  as an ordinary handler parameter.
- Preserve edition metadata describing source/release distinctions and fixed
  methodological constants. Keep declared output meaning explicit.
- Ensure family association is metadata, not a dependency edge. Equal schemas do
  not automatically create a family or merge concrete nodes.

## Acceptance and validation

- Build two factory-generated edition graphs with repeated local output names;
  confirm their concrete references remain separate unless sharing is explicit.
- Test implicit editions, a single named edition, multiple named editions,
  duplicate registration, and unknown or omitted named-edition selection at the
  available resolution boundary.
- Verify stable manifest round trips and concrete execution with fake providers.
- Run the index's Python checks and affected registration and graph tests.

## Boundary

Do not introduce a required graph-bundle abstraction or conditional per-edition
public parameters. Complete cross-variant structural validation in
[family validation](08-family-validation.md) and the shared public request path in
[request preparation](16-shared-request-preparation.md).
