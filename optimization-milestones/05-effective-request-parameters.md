# 05 · Capture effective parameters once

An accepted request carries the parameter values each graph invocation will receive, including defaults. Local preparation, queue transport, worker execution, provenance, and cache identity must agree on field names, values, and JSON scalar types.

## Registration and discovery

Require concrete `MetricParameters` subclasses for metric, operation, and nested parameter models. Support finite JSON types, field constraints, defaults, and default factories. Each field has one public name and one standard Pydantic JSON representation. Require unknown-field rejection, default validation, and descriptions on top-level fields. Validate the generated self-contained object schema and reject aliases and exclusions through public field metadata.

The authoring contract excludes computed fields, custom serializers or encoders, and structural schema overrides. Remove private decorator inspection and serializer class-name checks. Generate discovery schemas without executing factories or validators. At registration, reject a generated schema containing a nonfinite literal default such as infinity. At catalog startup, check from declarations that each required parameter on a canonical operation and its dependencies has a source. Remove the startup call that prepares canonical operations with empty parameters. Run value validators and evaluate defaults during preparation of a request that selects the route. For example, a finite default of `-1` on a field constrained to `ge=0` rejects a request that uses the default before enqueue. Other routes remain usable.

## Values and graph positions

Use strict Pydantic `JsonValue` with `allow_inf_nan=False` at request and identity boundaries. Preserve large integers and canonical hash ordering and encoding. Reject bytes, tuples, non-string object keys, and nested nonfinite numbers. Explicitly serialize internal validation-error details when constructing MCP JSON errors.

Field validators may return deterministically normalized values, such as a fresh sorted list. Authors must avoid in-place mutation, keep normalization idempotent, and use read-only cross-field validators. Adapt the tag normalization test to return a fresh sorted list from a field validator and verify the effective values after JSON transport. At acceptance, compare serialized parameters with the effective model fields recursively, including defaults, nulls, and exact scalar types. Reject lost or renamed fields and value or type changes. Use the verified values for forwarding and computation identity.

Store the root values once in `request.parameters` on both the prepared plan and the queued job. Remove the top-level `parameters` field, the stored root entry in `parameter_uses`, and the equality checks between those copies. Worker preparation and provenance read the same field. Preserve the distinction between no parameter model and an empty model. Assemble the complete mapping when binding.

Identify each non-root use by its parent use, receiving binding path, and applicable route hop. Give dependency edges and automatic hops distinct representations. Store parameters as values of this mapping. A long root list must not be copied into every sibling's key. Preserve independent values for siblings, nested bindings, and successive hops.

## Acceptance and handoff

Run two sibling inputs that invoke the same producer through worker serialization, binding, and execution. Verify that each invocation receives its own accepted values, including captured defaults. Worker revalidation against live models must reject changes in field names, values, or scalar types before handler execution. For a `float` field, inputs `1` and `1.0` that normalize to the same float share a key. For an `int | float` field that preserves the distinction, the keys differ. Include nested values and Boolean/integer distinctions in transport coverage.

Ship both queued-format changes together, update local and worker fixtures and parameter authoring guidance, and discard and resubmit jobs still queued in the previous format.
