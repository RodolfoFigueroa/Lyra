# Lyra maintenance ledger

Reviewed on 2026-11-18 at commit `0cc196d`. Lyra was in closed alpha with no existing clients or deployments. The review traced callers through the SDK, local runner, worker, storage, clients, tests, and benchmarks. It included an in-memory Arrow comparison. The test suite and benchmarks were not run, so this ledger makes no measured performance claims.

All entries are **open**. Each heading has a stable identifier. When resolving an entry, record the decision and commit or PR under that heading. Source links point into the working tree. The review commit preserves the evidence after files move or disappear.

**Suggested order.** Remove the unreachable executor branch and prototype planner first (M01–M02). Move graph tests onto the production compiler (M04) before simplifying provider identity (M05). Of the changes that relax a guarantee, start with producer validation and Arrow conversion (M07–M08).

## Dead and stale code

### M01 · Remove the unreachable executor branch

Preflight assigns a bound root for every `PreparedExecution`. Dispatch then returns through `_run_bound_metric`. The later `PreparedExecution` arm in `_execute_scoped` is unreachable. Its three private helpers, `_prepared_locations`, `_reuse_bound_operation`, and `_lookup_bound_operation`, total 169 lines and have no other callers. Removing the arm and its helpers accounts for roughly 200 lines. The executor still uses materialized invocations to call handlers. [Preflight](../src/lyra/runtime/execution.py#L1144) · [Dispatch](../src/lyra/runtime/execution.py#L625) · [Unreachable arm](../src/lyra/runtime/execution.py#L1214) · [Helper chain](../src/lyra/runtime/execution.py#L1338)

### M02 · Retire the prototype provider request planner

`prepare_provider_request` parses requests, compiles registrations, walks dependencies, and forwards parameters. Only its tests call it. The local runner and worker both call `prepare_execution`. Remove the prototype entry point, its request and result types, and its private collector. Keep `PreparedProviderInput` and `validate_provider_selections`, which production still uses. The same test module already exercises rejection before acquisition through `LocalRunner`. [Prototype](../src/lyra/runtime/planning/provider_preparation.py#L67) · [Live planner](../src/lyra/runtime/planning/preparation.py#L26) · [Acquisition caller](../src/lyra/runtime/provider_acquisition.py#L18) · [Behavioral tests to keep](../tests/test_provider_preparation.py#L283)

### M03 · Remove the standalone geometry codec and unused scope validator

`geometry_buffers.py` and `geometry_shapely.py` maintain 134 lines for a standalone WKB Arrow table, with custom rules for geometry types, dimensions, and empty components. Only tests and test helpers use this representation. Retained tables write WKB directly with Shapely. Move any required geometry round-trip assertions to that storage boundary and remove the standalone codec. The separate 21-line `data/location_scope.py` validator has no code callers. [Standalone codec](../src/lyra/data/geometry_buffers.py#L35) · [Geometry restrictions](../src/lyra/data/geometry_shapely.py#L18) · [Retained WKB writer](../src/lyra/data/relational.py#L86) · [Unused scope validator](../src/lyra/data/location_scope.py#L18)

## Duplicate implementations and compatibility bridges

### M04 · Exercise the production graph compiler in tests and benchmarks

Test support has its own plugin decorators and a 278-line manifest graph compiler. One test compiles a graph, mutates two input contracts, and reassembles it because the fixture rejects a route the live system supports. A catalog benchmark also times the fixture compiler. Build small plugins with the SDK and pass them through `compile_live_catalog`. Preserve assertions about cycles, routes, and bindings, and point the compilation benchmark at the path it is intended to measure. [Fixture decorators](../test_support/declarations.py#L55) · [Fixture compiler](../test_support/manifest_graph.py#L114) · [Test workaround](../tests/test_selected_execution_plans.py#L157) · [Benchmark](../benchmarks/catalog.py#L82) · [Production compiler](../src/lyra/runtime/planning/catalog.py#L60)

### M05 · Simplify the bridge from provider identity to collection and edition

Live registration writes the provider ID into both `collection` and `source`. It sets `unit_type` to `"record"` and `geographical_edition` to `"provider"`. The planner also resolves per-input `source_bindings` overrides that the SDK leaves empty and tests populate manually. Represent routing endpoints by provider identity and retain the schema, origins, revisions, and intervention scope that actually vary. This changes serialized descriptions and identity keys. Address M04 first so routing coverage exercises declarations an SDK author can create. [Provider adapter](../src/lyra/runtime/planning/provider_registry.py#L67) · [SDK binding](../src/lyra/sdk/plugin.py#L453) · [Override resolution](../src/lyra/runtime/planning/catalog.py#L211) · [Fixture-created overrides](../tests/test_accepted_sources.py#L57)

### M06 · Capture the provider registry once during catalog compilation

On the unbound path, definition binding builds the provider registry, node compilation builds it again, and final catalog assembly builds it a third time. Each pass collects resources, validates declarations, and captures provider schemas. Pass the captured registry through all three stages. Preserve registration validation, resource ownership, and detection of conflicting provider IDs. [Definition binding](../src/lyra/runtime/planning/native_bindings.py#L91) · [Node compilation](../src/lyra/runtime/planning/node_declarations.py#L86) · [Catalog assembly](../src/lyra/runtime/planning/catalog.py#L92)

## Library-backed simplifications and their tradeoffs

### M07 · Give Pandera one producer-table validation boundary

A provider frame passes through `ProviderSchema.validate`, `LocationOutputSchema.prepare_frame`, and the general result validator. Lyra repeatedly checks frame structure and nullability. The result validator also converts every declared integer value through `Decimal(str(value))` before and after Pandera validation.

Check opaque IDs and CRS before coercion. Let Pandera enforce declared dtypes, nullability, uniqueness, and plugin checks, then detach the result at the ownership boundary. For integer columns, require an integer input dtype or restrict the bounds check to inputs that need coercion. This preserves overflow protection where a cast could hide it. [Provider entry](../src/lyra/sdk/provider_schema.py#L73) · [Location checks](../src/lyra/sdk/location_schema.py#L63) · [Result validation](../src/lyra/sdk/results.py#L120) · [Integer scan](../src/lyra/sdk/results.py#L198) · [Pandera schemas](https://pandera.readthedocs.io/en/stable/dataframe_schemas.html)

### M08 · Limit Arrow conversion to supported output types

The converter builds arrays one column at a time and rewrites Arrow's pandas metadata. Both registration and download-schema construction convert empty frames to obtain metadata. The conversion tests cover unsigned integers, narrow integers, half-precision floats, and timestamps, while registered outputs admit a smaller set of types.

In the review's in-memory comparison, native `Table.from_pandas` preserved the tested values and nulls, a signed integer above 2⁵³, and the typed empty columns. A `string[python]` column came back as `string[pyarrow]` under the installed pandas and PyArrow configuration. Accepting that storage-backend change would allow Lyra to derive fields once for its supported logical types and use native conversion. Preserve tests for nullable integer precision, empty results, and spatial CRS. [Converter](../src/lyra/data/arrow_conversion.py#L30) · [Registered output types](../src/lyra/sdk/output_schema.py#L22) · [Registration schema](../src/lyra/sdk/storage_schema.py#L26) · [Download schema](../src/lyra/data/arrow_tables.py#L33) · [Test matrix](../tests/test_arrow_conversion.py#L36) · [PyArrow conversion](https://arrow.apache.org/docs/python/generated/pyarrow.Table.html#pyarrow.Table.from_pandas)

### M09 · Validate Pydantic's generated schema and effective parameter values

Before calling `model_json_schema`, Lyra inspects model inheritance, decorators, aliases, serializers, literals, unions, and nested annotations. Several checks depend on Pydantic internals or serializer class names. Keep Lyra-specific authoring rules such as parameter descriptions. Validate the generated schema used for discovery and the effective JSON values handed to the worker. Some unusual model customizations would then fail during value preparation or round-tripping. [Custom inspector](../src/lyra/sdk/parameters.py#L64) · [Schema generation and serialization](../src/lyra/sdk/parameters.py#L213) · [Pydantic JSON Schema](https://docs.pydantic.dev/latest/concepts/json_schema/)

### M10 · Trust SQLite scalar constraints on ordinary retained reads

Retained tables use SQLite `STRICT` typing, column nullability, Boolean constraints, and finite-float bounds. The Python read paths check physical types, nullability, and ranges again while reconstructing frames and encoding Arrow batches or JSON rows. Let SQLite enforce scalar storage. Keep bounded reads and the conversions visible to consumers, including exact integer strings in JSON and WKB decoding. This would reduce Lyra's ability to diagnose a store altered outside the application at the first malformed cell. [SQLite constraints](../src/lyra/runtime/sqlite_schema.py#L300) · [Read-time predicates](../src/lyra/data/relational.py#L104) · [Row encoding](../src/lyra/data/relational.py#L184) · [Arrow batches](../src/lyra/data/arrow_tables.py#L90)

### M11 · Use Arrow for both single and bulk DataFrame retrieval

`results.table()` reads Arrow. `results.tables()` fetches the complete job JSON and invokes a separate 154-line decoder to rebuild dtypes, geometry, and CRS. Keep the JSON response for JSON callers. The bulk DataFrame method could obtain the result reference and fetch each named output through the existing Arrow path. Bulk DataFrame users would need PyArrow and, with the current endpoints, one table request per output. [Single-table path](../src/lyra/api/client/sync.py#L276) · [Bulk path](../src/lyra/api/client/sync.py#L411) · [JSON decoder](../src/lyra/api/client/_tables.py#L26) · [Arrow decoder](../src/lyra/api/client/_arrow.py#L26)

## Historical tests

### M12 · Remove cases tied to retired config versions and field names

`test_old_configuration_fields_are_rejected` exercises versions 2 and 3 and the retired `plugins.repos` spelling. A separate test pins `plugins.metric_queues`. The suite already verifies the known-version rule and rejects arbitrary unknown nested fields. Those general cases cover the supported behavior. [Old versions and repos field](../tests/test_plugins.py#L101) · [Old queue field](../tests/test_config_contract.py#L341) · [Unknown nested fields](../tests/test_config_contract.py#L135) · [Version rule](../tests/test_config_contract.py#L195)

### M13 · Remove MCP cases tied to retired tools and wait arguments

The MCP suite invokes two retired tools, sends `wait_seconds` to current tools, and checks that every tool schema omits that name. It already exercises an invented unknown tool and an arbitrary unexpected argument. Remove the retired spellings and retain an assertion that rejected calls never reach the backend. [Retired cases](../tests/test_mcp_server.py#L1430) · [Schema absence assertion](../tests/test_mcp_server.py#L484) · [Generic rejection cases](../tests/test_mcp_server.py#L437)

## Migration check

The review found no database migration chain or reader for earlier config formats. SQLite initializes the current schema with `create_all`, and the config model requires schema version 5. The compatibility work identified above concerns internal representations and fixtures. [SQLite initialization](../src/lyra/runtime/sqlite_store.py#L187) · [Config version](../src/lyra/contracts/config.py#L501)
