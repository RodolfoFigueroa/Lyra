# Phase 19 — Dependency binding semantics

Read the [shared workflow](index.md). Continue after
[phase 18](18-canonical-route-execution.md). Apply ledger decisions **2, 3, 4, 7, 16, 17**.

Complete binding ownership and execution-plan validation around prepared native
inputs, particularly for nested calculation dependencies.

## Work

- Ensure every `Input(...)` reference remains associated with its declaration
  owner. It must read that owner's effective native input after canonical
  preparation, never the original caller source selection or another hop's input.
- Preserve ordinary explicit-binding precedence over matching-name forwarding,
  with producer defaults filling remaining parameters. Apply the same rules to
  nested explicit location dependencies.
- Keep canonical hop inputs explicit and selector defaults/fixed parameters
  isolated from coincidentally named metric parameters.
- Require explicit internal conversions when calculation contracts differ. Public
  selection routing must not rewrite methodological relationships.
- Validate selected routes together with declared calculation dependencies as an
  acyclic execution plan. Reject structurally invalid combinations during catalog
  compilation, while allowing cycles in the canonical conversion graph itself.
- Preserve useful owner, receiving-input, and producer context in binding errors.

## Acceptance and validation

- Convert a coarse public selection to a metric's native units, then verify nested
  dependencies reference those native units and the correct declaration owner.
- Cover explicit overrides, forwarded values, producer defaults, reused names
  across scopes, and selector/metric parameter-name collisions.
- Test explicit noncanonical dependencies and reject implicit internal rerouting.
- Include a valid cyclic conversion catalog and a route/dependency combination
  that would cause recursive execution; reject the latter before acquisition.
- Run the index's Python checks and binding, graph, and execution tests.

## Boundary

Do not expand the dependency language or introduce general graph synthesis. Resolve
integration defects in the existing binding model and complete its validation.
