# Phase 4: Operations and routes

## Objective and prerequisites

Retain operation-derived location inputs from fresh execution, including automatic
routing. Complete [phase 3](03-fresh-direct-inputs.md), read its handoff and phase 2's
storage decisions, and follow the [overview](index.md). Read the logical-selection
and correspondence examples in
[milestone 12](../feature-milestones/12-retained-location-inputs.md), along with
[milestone 6's binding rules](../feature-milestones/06-unified-graph-execution.md).

Direct capture and local reconstruction already exist. Extend those mechanisms
with derived bindings rather than storing a second kind of frame snapshot.

## Implementation

Capture every named root location input supplied through an explicit operation,
a source route, or a receiving-input route. Retained intermediate metrics need
the same reconstruction references for their own operation-derived inputs.
Capture selected pairs and forwarding descriptions before downstream handlers can
mutate their arguments.

Retain the immediate source scope, target scope, and complete selected pairs under
existing operation computation identities. Retain target records separately under
their provider identities. For sources A and B selecting T, the read returns
`(A, T)` and `(B, T)` with T's geometry and attributes in both rows. It must not
collapse to one target row. A route A → P → T returns `(P, T)` for its final input,
with the parcel source scope and tree target scope, without ancestral expansion
or source geometry.

Preserve selected groups when a consumer uses only part of a larger operation
result. A retained full A/B/C mapping does not authorize exposing C's unrelated
pairs to an A/B input. Account for target-based selection of a derived primary
input as well as source-related groups. The storage references must identify the
logical selection actually bound to the metric.

Persist mappings and target records required for inspection even when operation
or provider caching is off. Separate that retention obligation from execution
reuse decisions: a later uncached computation still follows ordinary scheduling
and acquisition policy. Do not enable cache reads merely because an inspection
reference now points at a stored value.

Store successful zero-match groups as completed empty correspondences. They
reconstruct with the forwarding schema, both scopes, active geometry, and CRS.
Unresolved operation membership is not a completed empty mapping. An operation
that ran and selected nothing is different from an operation skipped before its
selection was resolved; the latter receives final availability handling in phase 7.

Apply the same required-persistence boundary as direct inputs. Missing required
pairs or targets cause integrity errors, never reexecution of selectors, predicate
queries, providers, or operation handlers during inspection. Avoid storing full
candidate pools, rejected pairs, joined geometries, or copies per invocation.

## Starting points

- [Executor](../src/lyra/runtime/execution.py): bound correspondences,
  materialization, route execution, derived inputs, forwarding, and operation
  publication paths.
- [Operation handling](../src/lyra/runtime/operations.py),
  [operation pairs](../src/lyra/runtime/operation_pairs.py), and
  [provider records](../src/lyra/runtime/provider_records.py).
- [Native lineage](../src/lyra/runtime/native_inputs.py) and
  [bound selection](../src/lyra/runtime/planning/bindings.py).

## Verification and completion

Cover explicit operations, automatic source and receiving routes, repeated targets,
multi-hop routes, direct and derived references on one metric, selected groups
from a full producer, and completed zero matches. Use equal bare IDs in different
provider roles and revisions to catch incorrect merging. Exercise selector-backed
operations with in-process query/acquisition fakes.

Verify target attributes and CRS, handler mutation isolation, separate pair and
record retention, and persistence failures. After execution, forbid acquisition
and operation/selector calls during local description and value reads. A final
input read must reconstruct from the retained final-hop selection alone, without
traversing historical execution reports.

Run the overview's Python checks and relevant coverage:

```sh
uv run pytest -n auto tests/test_operation_pairs.py tests/test_location_operations.py tests/test_receiving_route_execution.py tests/test_operation_cross_path.py tests/test_native_ownership_equality.py
```

Add retained-input assertions for the new scenarios. Completion means fresh
derived inputs use the same durable description/read contract as direct inputs
across both execution paths. Cache restoration and mixed assembly follow next.

## Handoff

Document how captured references encode immediate scopes, completed empty groups,
selected subsets, and any necessary target restrictions in
[phase 5](05-complete-cache-reuse.md) and
[phase 6](06-partial-reuse-and-group-assembly.md). Record unresolved derived
availability cases in [phase 7](07-availability-and-skipped-work.md), including
the concrete tests that fail. Keep any remaining cache-policy issue assigned to
the phase that will fix it; do not treat inspection retention as reuse permission.
