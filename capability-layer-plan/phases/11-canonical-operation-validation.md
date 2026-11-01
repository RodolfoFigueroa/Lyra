# Phase 11 — Canonical-operation validation

Read the [shared workflow](index.md). Continue after
[phase 10](10-shared-catalog-compilation.md). Apply ledger decisions **4, 7, 9, 17**.

Allow authors to opt eligible location operations into automatic routing, without
changing the role of ordinary explicit dependencies.

## Work

- Add explicit canonical status to operation declarations and manifests. Default
  ordinary operations to ineligible for automatic routing.
- Require a canonical operation to have exactly one named location-set input and
  one location-set output with concrete contracts.
- Permit resources, context, defaults, fixed parameters, and internal dependencies
  when the entire invocation is satisfiable from that input and declared bindings.
  Validate the transitive bindings rather than only the operation's immediate
  signature.
- Reject any canonical declaration requiring extra caller-supplied locations or
  ordinary parameters. Selector defaults and fixed bindings must stand on their
  own, independently of the consuming metric's parameters.
- Keep noncanonical operations usable as explicitly bound internal dependencies.
  Their presence inside a canonical operation does not make them routing edges.
- Expose validated typed edges in the compiled catalog. A plugin may contribute
  no canonical operations at all.

## Acceptance and validation

- Test eligible operations with defaults, resources, context, and fully bound
  dependencies, including dependencies on noncanonical operations.
- Reject extra unbound inputs, unsatisfied nested parameters, and invalid contracts
  during compilation, without constructing resource clients.
- Check that direct native execution and explicit location dependencies still
  work in catalogs without canonical edges.
- Run the index's Python checks and affected declaration and graph tests.

## Boundary

Do not choose routes yet. Conversion-graph cycles are permitted; ordinary recursive
execution dependencies remain invalid. Keep those two graph concepts distinct.
