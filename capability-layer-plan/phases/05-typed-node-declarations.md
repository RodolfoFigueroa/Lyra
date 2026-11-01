# Phase 05 — Typed node declarations

Read the [shared workflow](index.md). Continue after
[phase 04](04-provider-contracts.md). Apply ledger decisions **1, 2, 3, 13**.

Declare the concrete contract of every named location input and location-operation
output alongside its node definition.

## Work

- Support multiple named location inputs, each with one fixed concrete contract.
  Reuse references to family schemas and provider contracts rather than copying
  their definitions into every node. Generate manifests and schemas from node
  declarations without a separate publication step.
- Declare a location operation's concrete output contract. Keep ordinary
  parameters, resources, context, and dependency arguments distinct from location
  inputs.
- Preserve descriptions, field definitions, units, and author-declared metric
  output meaning and granularity in compiled metadata. A source selection must not
  redefine the metric's subject roles or output meaning.
- Validate supplied resolved values against the receiving contract, and validate
  operation results against their output contract. Handlers continue receiving
  ordinary resolved location values.
- Update affected authoring and manifest paths, callers, and examples directly.

## Acceptance and validation

- Test a metric with several differently typed named inputs and an operation with
  a different target contract.
- Reject incompatible source contracts even when geographic unit identity or
  structural schemas match.
- Check that declared and provider contracts agree and that serialized metadata
  preserves the declarations needed for later compilation.
- Run the index's Python checks and affected declaration, schema, and operation
  tests.

## Boundary

Do not implement canonical routing or infer conversions for mismatched inputs.
Canonical eligibility is a later concern; typed explicit operations must remain
useful independently.
