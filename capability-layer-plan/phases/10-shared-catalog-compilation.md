# Phase 10 — Shared catalog compilation

Read the [shared workflow](index.md). Continue after
[phase 09](09-metric-visibility.md). Apply ledger decisions **1, 5, 13, 16, 23**.

Give application startup and local execution the same compiled understanding of
enabled definitions, families, contracts, and dependencies.

## Work

- Consolidate shared compilation of the complete enabled catalog. Include concrete
  graph identities, family contracts, visibility, provider bindings, and validated
  references. Preserve application-specific details such as queue assignment in
  their appropriate layer.
- Assemble cross-plugin relationships before final validation. Do not assume each
  plugin supplies all of its eventual canonical conversions or dependencies.
- Make compiled metadata stable for a catalog's lifetime and suitable for reuse by
  local preparation, remote preparation, and workers. Avoid resource acquisition
  during compilation.
- Wire current application and local catalog construction to the shared result,
  removing duplicated contract interpretation where practical.
- Preserve the information needed to extend compilation with canonical routes and
  revision identities in later phases; do not implement speculative interfaces.

## Acceptance and validation

- The same enabled definitions yield equivalent executable contracts and errors in
  local and application contexts.
- Test cross-plugin references, duplicate identities, conflicting declarations,
  invalid family contracts, and dependency cycles.
- Verify compilation performs no provider acquisition or external service access.
- Run the index's Python checks and affected catalog/local/application tests.
  Measure a representative catalog compilation and local-initialization checkpoint,
  including multiple edition families where available.

## Boundary

This is consolidation of established behavior. Routing algorithms, accepted source
projection, and public discovery views are implemented in their later phases.
