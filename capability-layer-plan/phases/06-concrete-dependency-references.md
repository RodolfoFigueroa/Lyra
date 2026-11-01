# Phase 06 — Concrete dependency references

Read the [shared workflow](index.md). Continue after
[phase 05](05-typed-node-declarations.md). Apply ledger decisions **2, 14, 16, 17**.

Make dependency identity explicit enough for multiple concrete variants to expose
the same output names without ambiguity.

## Work

- Bind table dependencies through concrete producer/output references. Bind
  location dependencies through concrete operation references. Preserve these
  identities through manifests, graph construction, and invocation preparation.
- Retain the declaration owner and receiving input for binding references,
  including nested location dependencies. Do not resolve an unqualified output
  name globally across producers.
- Propagate concrete location contracts through explicit bindings and matching-name
  forwarding. Reject incompatible contracts rather than adding automatic routing
  inside calculation dependencies.
- Keep explicit overrides, parameter forwarding, and producer defaults working.
  Validate missing producers, missing outputs, unsatisfied inputs, and dependency
  cycles with useful receiving-input context.

## Acceptance and validation

- Two producers can expose the same output name while a consumer unambiguously
  selects either one.
- Test cross-plugin references, nested bindings, shared dependencies, malformed
  references, incompatible locations, and cycles without live resources.
- Verify that concrete references survive serialization and execution, with
  shared dependency reuse preserved.
- Run the index's Python checks and affected graph, argument-binding, and executor
  tests.

## Boundary

Do not build metric edition families yet or add canonical routing. This phase
establishes the reference and ownership behavior that later factory-built graphs
and public input conversion will use.
