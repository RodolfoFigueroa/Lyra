# 01 · Exercise the production planner and compiler

A routing test should describe a graph that a plugin author can actually register. Express test and benchmark graphs as small SDK plugin declarations, compile them with `compile_live_catalog`, and exercise requests through `prepare_execution` or `LocalRunner`. This gives the routing and executor refactors a behavioral baseline.

## Graphs and prepared requests

Replace the fixture-only decorators and manifest compiler in `test_support`. Preserve cases for route preferences, ambiguous routes, dependency bindings, and cycles. One important case has two individually valid routes whose joint selection introduces a cycle. Drop assertions that require injecting `source_bindings` an SDK plugin cannot declare. Have the accepted-sources benchmark in `benchmarks/catalog.py` measure live compilation as provider and metric counts grow.

Remove `prepare_provider_request`, its private collector, and its dedicated request and result types. Production still uses `PreparedProviderInput` and `validate_provider_selections`. In `GraphExecutor`, preflight binds every prepared root and dispatches it through `_run_bound_metric`. Delete the unreachable prepared arm of `_execute_scoped` and its helpers `_prepared_locations`, `_reuse_bound_operation`, and `_lookup_bound_operation`. Handlers still receive materialized invocations.

## Supported behavior to retain

- Exercise empty and repeated selections through the live planner. Invalid selections fail before acquisition. Mutating the caller's input after preparation leaves the plan intact. Nested uses of the same operation receive their own accepted parameters.
- Keep generic coverage for unsupported config versions, unknown nested fields, unknown MCP tools, and unexpected arguments. Rejected requests must stop before the relevant backend, enqueue, or acquisition call. Remove cases tied to config versions 2 and 3, `plugins.repos`, `plugins.metric_queues`, retired MCP tools, and `wait_seconds`, including assertions about its absence from tool schemas. Remove `cache_revision` rejection matrices across clients, local runs, planning, worker handoff, HTTP, and MCP, along with assertions about its absence from provenance. Keep revision-based cache reuse and invalidation coverage.
- Delete `ExplicitLocationAPI`, `ExplicitBoundsAPI`, their marker constants, and cases dedicated to the former bounds annotation or `spatial_inputs`. Keep GeoDataFrame handler inputs, resource/location name-collision checks, and the GeoJSON models used by geometry utilities.
- Remove `StoredOutputInspection`, `GraphExecutor.persistent_key`, and `normalize_property`. Cache tests can use the computation identity directly. Drop tests specific to the scalar normalizer. Result serialization still needs `date_property`. Retain the supported assertions from mixed tests when removing fixture-specific cases.
