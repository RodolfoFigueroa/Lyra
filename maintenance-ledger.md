# Lyra maintenance ledger

Reviewed on 2026-11-18 at commit `0cc196d`. Lyra was in closed alpha with no existing clients or deployments. The review traced callers through the SDK, local runner, worker, storage, clients, tests, and benchmarks. It included an in-memory Arrow comparison. The test suite and benchmarks were not run, so this ledger makes no measured performance claims.

Each heading has a stable identifier. When resolving an entry, record the implementing commit or PR under that heading. Source links point into the working tree at the line numbers from the corresponding review.

**Suggested order.** Remove the unreachable executor branch and prototype planner first (M01–M02). Move graph tests onto the production compiler (M04) before simplifying provider identity (M05). Of the changes that relax a guarantee, start with producer validation and Arrow conversion (M07–M08).

Among the workflow changes below, GeoParquet export (M17) is the smallest independent cleanup. Metric cache scheduling (M14) has the highest priority before adding another cache policy.

Start the third group with generated job IDs (M19) and on-demand result previews (M20). Both can ship independently of the durable run ID change (M25). Remove per-metric queue routing (M26) alongside the worker catalog cleanup (M23).

## Acceptance status

| ID | Recommendation | Status |
| --- | --- | --- |
| M01 | Remove the unreachable executor branch | accepted |
| M02 | Retire the prototype provider request planner | accepted |
| M03 | Remove the standalone geometry codec and unused scope validator | accepted |
| M04 | Exercise the production graph compiler in tests and benchmarks | accepted |
| M05 | Simplify the bridge from provider identity to collection and edition | accepted |
| M06 | Capture the provider registry once during catalog compilation | accepted |
| M07 | Give Pandera one producer-table validation boundary | accepted |
| M08 | Limit Arrow conversion to supported output types | under review |
| M09 | Validate Pydantic's generated schema and effective parameter values | under review |
| M10 | Trust SQLite scalar constraints on ordinary retained reads | under review |
| M11 | Use Arrow for both single and bulk DataFrame retrieval | under review |
| M12 | Remove cases tied to retired config versions and field names | accepted |
| M13 | Remove MCP cases tied to retired tools and wait arguments | accepted |
| M14 | Let the metric scheduler own cache lookup | under review |
| M15 | Share job submission and waiting across client methods | accepted |
| M16 | Share exact-ID and intervention queries across managed providers | accepted |
| M17 | Assemble GeoParquet export metadata once | under review |
| M18 | Use one catalog pagination path for HTTP and MCP | under review |
| M19 | Use the job ID format Lyra generates | under review |
| M20 | Compute result previews only for inspection | under review |
| M21 | Reuse the published report and avoid rebuilding output tables at publication | under review |
| M22 | Measure the finished row page against the byte limit | under review |
| M23 | Load the worker catalog through the application loader | under review |
| M24 | Retire the SQLite publication helper used by tests and benchmarks | under review |
| M25 | Use one durable run ID for the report and outputs | under review |
| M26 | Use one shared RQ queue per deployment | under review |

## Dead and stale code

### M01 · Remove the unreachable executor branch

Preflight assigns a bound root for every `PreparedExecution`. Dispatch then returns through `_run_bound_metric`. The later `PreparedExecution` arm in `_execute_scoped` is unreachable. Its three private helpers, `_prepared_locations`, `_reuse_bound_operation`, and `_lookup_bound_operation`, total 169 lines and have no other callers. Removing the arm and its helpers accounts for roughly 200 lines. The executor still uses materialized invocations to call handlers. [Preflight](src/lyra/runtime/execution.py#L1144) · [Dispatch](src/lyra/runtime/execution.py#L625) · [Unreachable arm](src/lyra/runtime/execution.py#L1214) · [Helper chain](src/lyra/runtime/execution.py#L1338)

### M02 · Retire the prototype provider request planner

`prepare_provider_request` parses requests, compiles registrations, walks dependencies, and forwards parameters. Only its tests call it. The local runner and worker both call `prepare_execution`. Remove the prototype entry point, its request and result types, and its private collector. Keep `PreparedProviderInput` and `validate_provider_selections`, which production still uses. [Prototype](src/lyra/runtime/planning/provider_preparation.py#L67) · [Live planner](src/lyra/runtime/planning/preparation.py#L26) · [Acquisition caller](src/lyra/runtime/provider_acquisition.py#L18)

Before deleting the prototype tests, check which supported behaviors already have coverage through `prepare_execution` or `LocalRunner`. Existing tests cover empty and repeated selections and rejection before acquisition. Move any uncovered assertions to those production paths, including checks that caller mutations leave a prepared plan intact and that nested uses of the same operation receive their own parameter values. [Selection coverage](tests/test_dependency_route_planning.py#L221) · [Rejection before acquisition](tests/test_provider_preparation.py#L283) · [Caller mutations](tests/test_provider_preparation.py#L155) · [Nested parameters](tests/test_provider_preparation.py#L207)

### M03 · Remove the standalone geometry codec and unused scope validator

Only tests and test helpers use the standalone WKB Arrow table in `geometry_buffers.py` and `geometry_shapely.py`. Remove both modules, the tests specific to that representation, and the unused `data/location_scope.py` validator. [Standalone codec](src/lyra/data/geometry_buffers.py#L35) · [Codec rules](src/lyra/data/geometry_shapely.py#L18) · [Unused scope validator](src/lyra/data/location_scope.py#L18)

Retained tables write and read WKB directly with Shapely. Test that geometry values round-trip through this path, that the restored frame retains its declared CRS, and that an empty geometry remains distinct from null when the output schema permits both. Providers can enforce stricter geometry rules in their schemas. [Retained WKB writer](src/lyra/data/relational.py#L86) · [Storage round-trip test](tests/test_relational_conversion.py#L79) · [Result contract](docs/src/content/docs/use/results.md#read-a-retained-table) · [Provider schema check](tests/test_provider_schema.py#L143)

## Duplicate implementations and compatibility bridges

### M04 · Exercise the production graph compiler in tests and benchmarks

Replace the test-only plugin decorators and manifest graph compiler with small SDK plugin declarations compiled through `compile_live_catalog`. Preserve coverage for route selection, ambiguous routes, dependency bindings, and cycle rejection. For example, declare two routes that are valid individually and verify that selecting them together fails when they form a cycle. [Fixture decorators](test_support/declarations.py#L55) · [Fixture compiler](test_support/manifest_graph.py#L114) · [Cycle test](tests/test_selected_execution_plans.py#L138) · [Live compiler test](tests/test_dependency_route_planning.py#L27)

Remove assertions that depend solely on overrides the SDK cannot express, such as hand-populated `source_bindings`. Keep any routing assertions in the same tests that SDK declarations can exercise. Update the accepted-sources benchmark to time `compile_live_catalog` as the number of registered providers and metrics grows. M05 covers changes to provider identity separately. [Override tests](tests/test_accepted_sources.py#L57) · [Accepted-sources benchmark](benchmarks/catalog.py#L75) · [Production compiler](src/lyra/runtime/planning/catalog.py#L60)

### M05 · Simplify the bridge from provider identity to collection and edition

Live registration copies the provider ID into both `collection` and `source`, sets `unit_type` to `"record"`, and sets `geographical_edition` to `"provider"`. Complete M04 first so routing tests exercise SDK declarations. [Provider adapter](src/lyra/runtime/planning/provider_registry.py#L67) · [SDK binding](src/lyra/sdk/plugin.py#L453)

- **Routing.** Use provider IDs for the source and target of each route. Remove the redundant collection, source, unit type, and edition fields from routing contracts and their serialized descriptions. Remove per-input `source_bindings` and their resolution logic. Keep SDK route preferences for choosing between equally short operation paths. [Override resolution](src/lyra/runtime/planning/catalog.py#L211) · [Route compiler](src/lyra/runtime/planning/catalog.py#L312)
- **Execution and caching.** Preserve the provider ID, provider code revision, origin IDs and revisions, and applicable intervention identity in dataset scope. A new origin revision for the same provider keeps the route valid and gives affected computations distinct cache keys. Editorial changes to provider or origin descriptions leave cache identity unchanged. [Provider scope](src/lyra/contracts/provider_scope.py#L33) · [Intervention scope](src/lyra/contracts/provider_scope.py#L91)
- **Discovery.** For each metric location input, expose the target provider ID and accepted source provider IDs. Keep the record schema, CRS, and query and membership capabilities available in the provider description. [Metric inspection](src/lyra/runtime/planning/discovery.py#L100) · [Provider declaration](src/lyra/contracts/providers.py#L26)

### M06 · Capture the provider registry once during catalog compilation

Build one fresh provider registry from all active plugins for each catalog build. Reuse it when binding plugin definitions, compiling nodes, constructing each plugin’s manifest, and assembling the final catalog. This includes server and worker startup, where the installed-plugin loader begins binding before catalog assembly. Validate resource requirements, preserve each plugin’s resource ownership, and reject conflicting provider IDs during the build. [Installed-plugin loader](src/lyra/server/plugins.py#L83) · [Definition binding](src/lyra/runtime/planning/native_bindings.py#L91) · [Node compilation](src/lyra/runtime/planning/node_declarations.py#L86) · [Manifest and catalog assembly](src/lyra/runtime/planning/catalog.py#L78) · [Registration rules](src/lyra/runtime/planning/provider_registry.py#L86)

Keep the execution-time check for intervention targets. Before acquiring a resource, verify that the concrete provider still matches the declaration accepted for that job. [Intervention target validation](src/lyra/runtime/execution.py#L372)

## Library-backed simplifications and their tradeoffs

### M07 · Give Pandera one producer-table validation boundary

At plugin registration, reject `coerce=True` on a metric output or provider schema and on any of its columns. Apply this rule to both `DataFrameSchema` and `DataFrameModel` declarations. Authors convert source values before returning the frame. At validation time, let Pandera enforce the declared dtypes, nullability, and uniqueness. A validated signed 64-bit integer dtype guarantees its range, so remove the per-value `Decimal` scans. [Schema registration](src/lyra/sdk/output_schema.py#L124) · [Provider registration](src/lyra/sdk/provider_schema.py#L29) · [Integer scan](src/lyra/sdk/results.py#L198)

Treat plugin checks as read-only predicates and run them in one Pandera validation pass per producer frame. Remove repeated validation and tests whose sole purpose is handling mutations by those callbacks. Check Lyra’s rules for opaque location IDs, active geometry, CRS, and serializable values once. Give the runtime an owned frame so later edits to the producer’s original cannot affect it. [Location checks](src/lyra/sdk/location_schema.py#L63) · [Result validation](src/lyra/sdk/results.py#L120) · [Serialization rules](src/lyra/data/results.py#L32) · [Mutation tests](tests/test_location_ingestion.py#L28)

### M08 · Limit Arrow conversion to supported output types

The converter builds arrays one column at a time and rewrites Arrow's pandas metadata. Both registration and download-schema construction convert empty frames to obtain metadata. The conversion tests cover unsigned integers, narrow integers, half-precision floats, and timestamps, while registered outputs admit a smaller set of types.

In the review's in-memory comparison, native `Table.from_pandas` preserved the tested values and nulls, a signed integer above 2⁵³, and the typed empty columns. A `string[python]` column came back as `string[pyarrow]` under the installed pandas and PyArrow configuration. Accepting that storage-backend change would allow Lyra to derive fields once for its supported logical types and use native conversion. Preserve tests for nullable integer precision, empty results, and spatial CRS. [Converter](src/lyra/data/arrow_conversion.py#L30) · [Registered output types](src/lyra/sdk/output_schema.py#L22) · [Registration schema](src/lyra/sdk/storage_schema.py#L26) · [Download schema](src/lyra/data/arrow_tables.py#L33) · [Test matrix](tests/test_arrow_conversion.py#L36) · [PyArrow conversion](https://arrow.apache.org/docs/python/generated/pyarrow.Table.html#pyarrow.Table.from_pandas)

### M09 · Validate Pydantic's generated schema and effective parameter values

Before calling `model_json_schema`, Lyra inspects model inheritance, decorators, aliases, serializers, literals, unions, and nested annotations. Several checks depend on Pydantic internals or serializer class names. Keep Lyra-specific authoring rules such as parameter descriptions. Validate the generated schema used for discovery and the effective JSON values handed to the worker. Some unusual model customizations would then fail during value preparation or round-tripping. [Custom inspector](src/lyra/sdk/parameters.py#L64) · [Schema generation and serialization](src/lyra/sdk/parameters.py#L213) · [Pydantic JSON Schema](https://docs.pydantic.dev/latest/concepts/json_schema/)

### M10 · Trust SQLite scalar constraints on ordinary retained reads

Retained tables use SQLite `STRICT` typing, column nullability, Boolean constraints, and finite-float bounds. The Python read paths check physical types, nullability, and ranges again while reconstructing frames and encoding Arrow batches or JSON rows. Let SQLite enforce scalar storage. Keep bounded reads and the conversions visible to consumers, including exact integer strings in JSON and WKB decoding. This would reduce Lyra's ability to diagnose a store altered outside the application at the first malformed cell. [SQLite constraints](src/lyra/runtime/sqlite_schema.py#L300) · [Read-time predicates](src/lyra/data/relational.py#L104) · [Row encoding](src/lyra/data/relational.py#L184) · [Arrow batches](src/lyra/data/arrow_tables.py#L90)

### M11 · Use Arrow for both single and bulk DataFrame retrieval

`results.table()` reads Arrow. `results.tables()` fetches the complete job JSON and invokes a separate 154-line decoder to rebuild dtypes, geometry, and CRS. Keep the JSON response for JSON callers. The bulk DataFrame method could obtain the result reference and fetch each named output through the existing Arrow path. Bulk DataFrame users would need PyArrow and, with the current endpoints, one table request per output. [Single-table path](src/lyra/api/client/sync.py#L276) · [Bulk path](src/lyra/api/client/sync.py#L411) · [JSON decoder](src/lyra/api/client/_tables.py#L26) · [Arrow decoder](src/lyra/api/client/_arrow.py#L26)

## Historical tests

### M12 · Remove cases tied to retired config versions and field names

`test_old_configuration_fields_are_rejected` exercises versions 2 and 3 and the retired `plugins.repos` spelling. A separate test pins `plugins.metric_queues`. The suite already verifies the known-version rule and rejects arbitrary unknown nested fields. Those general cases cover the supported behavior. [Old versions and repos field](tests/test_plugins.py#L101) · [Old queue field](tests/test_config_contract.py#L341) · [Unknown nested fields](tests/test_config_contract.py#L135) · [Version rule](tests/test_config_contract.py#L195)

### M13 · Remove MCP cases tied to retired tools and wait arguments

The MCP suite invokes two retired tools, sends `wait_seconds` to current tools, and checks that every tool schema omits that name. It already exercises an invented unknown tool and an arbitrary unexpected argument. Remove the retired spellings and retain an assertion that rejected calls never reach the backend. [Retired cases](tests/test_mcp_server.py#L1430) · [Schema absence assertion](tests/test_mcp_server.py#L484) · [Generic rejection cases](tests/test_mcp_server.py#L437)

## Parallel workflows

The follow-up review examined these workflows at commit `16796a3`. M14 includes an execution check using the in-memory storage fake. M15–M18 are based on source inspection.

### M14 · Let the metric scheduler own cache lookup

`_bound_metric` looks up the requested cache keys and materializes inputs for the missing partitions. It then calls the metric execution path, where `_metric` can look up those same keys again. In a `LocalRunner` check with a single full-cache metric, the cold run looked up one key twice and called the handler once. The warm run looked up the key once and skipped the handler. [Scheduler lookup](src/lyra/runtime/execution.py#L947) · [Execution lookup](src/lyra/runtime/execution.py#L1850) · [In-memory storage fake](tests/sqlite_helpers.py#L31)

Pass the scheduler’s missing partitions into execution and assemble cached and newly computed outputs in one place. Full hits should still skip input acquisition, and partial hits should acquire only the missing inputs. Keep the check inside the publication transaction because another worker may have published the same key since scheduling. [Partial reuse coverage](tests/test_partial_metric_reuse.py#L33) · [Transactional check](src/lyra/runtime/metric_tables.py#L63)

### M15 · Share job submission and waiting across client methods

In both Python clients, `run()` and `run_to_file()` submit a job, construct a polling deadline, wait for completion, and check the remaining time around the final request. That gives four places to update the same workflow. Share the submit-and-wait sequence within each client and centralize the deadline rules in the existing `PollingState`. The final request can still return a decoded result or stream to a file. Keep transport I/O native to each client and preserve support for awaitable progress callbacks. [Sync methods](src/lyra/api/client/sync.py#L485) · [Async methods](src/lyra/api/client/async_.py#L494) · [Polling state](src/lyra/api/client/polling.py#L21) · [Async callback coverage](tests/test_client_policies.py#L371)

### M16 · Share exact-ID and intervention queries across managed providers

PostGIS and Parquet both resolve exact IDs by filtering the base relation on `location_id`. When a selector uses an intervention, both adapters stage the additions and removals on first use, filter removed IDs out of the base, append the additions, and reuse the resulting relation. Move those common query steps into `ManagedLocationProvider`. Each adapter should still own its temporary objects, geometry conversion, and backend-specific materialization. [Shared provider](src/lyra/sdk/managed_provider.py#L25) · [PostGIS lookup](src/lyra/sdk/postgis.py#L130) · [Parquet lookup](src/lyra/sdk/parquet.py#L282) · [PostGIS intervention](src/lyra/sdk/postgis.py#L191) · [Parquet intervention](src/lyra/sdk/parquet.py#L333)

### M17 · Assemble GeoParquet export metadata once

The local result store and the sync and async clients each have two export methods, one for metric outputs and one for location inputs. All six call the same writer, but each selects the retained description and builds the metadata embedded in the file. Move that work next to `write_geoparquet`. Each caller can fetch the frame and description through its existing interface. This preserves the public methods, the distinction between input and output metadata, and the file format. [Local exports](src/lyra/runtime/result_store.py#L168) · [Sync exports](src/lyra/api/client/sync.py#L286) · [Async exports](src/lyra/api/client/async_.py#L293) · [Shared writer](src/lyra/data/geoparquet.py#L36)

### M18 · Use one catalog pagination path for HTTP and MCP

HTTP passes an offset and limit to discovery. For each page, MCP requests the full public metric list, slices it, and encodes the next offset and catalog fingerprint in a versioned cursor. Give discovery one operation that returns the requested page and total count, and use it from both interfaces. The MCP tool could also expose a plain offset and retire its cursor codec. That would drop automatic rejection when the catalog changes between pages, so a caller could skip or repeat a metric. Sharing page selection is possible with either cursor policy. [HTTP listing](src/lyra/server/routes/metrics.py#L14) · [Discovery pagination](src/lyra/runtime/planning/discovery.py#L70) · [MCP listing](src/lyra/server/mcp/tools.py#L299) · [Cursor codec](src/lyra/server/mcp/tools.py#L432)

## Jobs, results, and worker startup

Reviewed at commit `ef46012`. M19–M22 include isolated checks using the installed queue library, an in-memory store, or the HTTP page encoder. M23–M25 are based on the production callers and storage schema.

### M19 · Use the job ID format Lyra generates

Submission generates a 32-character lowercase hexadecimal UUID. The HTTP router nevertheless reconstructs raw paths and proxy prefixes to support IDs containing slashes, dot segments, and whitespace. The installed RQ validator rejected all 11 unusual IDs in the routing test matrix. Restrict the job ID contract to the generated format and use ordinary FastAPI path routing. Keep coverage for valid IDs, missing jobs, and authorization. This narrows the accepted HTTP and client inputs. [ID generation](src/lyra/server/job_submission.py#L120) · [Custom router](src/lyra/server/routes/job_ids.py#L11) · [Queue lookup](src/lyra/server/job_store.py#L184) · [Test matrix](tests/test_http_job_boundaries.py#L271)

### M20 · Compute result previews only for inspection

In an isolated call, the MCP download tool read the full retained result and computed its table summaries to return a URL. The HTTP full-result endpoint also goes through that preview step before serializing the tables. Use the job metadata lookup for the download handoff and compute previews in the inspection path. Preserve the pending and failed job responses. If a stored table is damaged, the handoff can succeed and the download itself will report the failure. [MCP download handoff](src/lyra/server/mcp/tools.py#L386) · [Job metadata lookup](src/lyra/server/job_store.py#L204) · [Preview computation](src/lyra/server/results.py#L301) · [HTTP full result](src/lyra/server/routes/jobs.py#L151)

### M21 · Reuse the published report and avoid rebuilding output tables at publication

A two-output `LocalRunner` check reconstructed both 17-row DataFrames inside the write transaction and discarded them. It also built the execution report twice for the same result reference. At publication, verify output names, scope, and recorded row counts through the existing selection checks. Return the report used for the committed write to the local caller. Some malformed cells would first be detected when their values are read. [Publication transaction](src/lyra/runtime/result_descriptions.py#L575) · [Frame reconstruction](src/lyra/runtime/result_descriptions.py#L543) · [Selection checks](src/lyra/runtime/output_selection.py#L149) · [Report creation](src/lyra/runtime/execution.py#L1331) · [Local return](src/lyra/runtime/local.py#L298)

### M22 · Measure the finished row page against the byte limit

For a 100-row HTTP page that fit the byte limit, the builder performed 101 size checks. Serializing every growing prefix and then the final page sent 5,150 row entries through the encoder. Use a conservative byte estimate while collecting rows, measure the finished response with the transport encoder, and shorten it if necessary. Pages may contain fewer rows. Preserve the hard byte limit, the cursor after the last returned row, and the download link when one row exceeds the limit. [Page builder](src/lyra/server/result_rows.py#L71) · [HTTP sizing](src/lyra/server/response_budget.py#L61) · [MCP sizing](src/lyra/server/mcp/serialization.py#L28) · [Pagination coverage](tests/test_result_row_pages.py#L82)

### M23 · Load the worker catalog through the application loader

Worker startup repeats plugin loading and catalog compilation. It walks the dependency graph, discards the result, and gives each selected metric the same full implementation map. With one queue per deployment (M26), use `Catalog.load` at worker startup and resolve the requested metric and its dependencies from that catalog. Remove the pool filter and the comparison with the job’s originating queue. Keep the checks that the worker can execute the accepted computation against its live catalog. [Application loader](src/lyra/server/registry.py#L63) · [Worker loader](src/lyra/server/worker.py#L170) · [Execution checks](src/lyra/server/worker.py#L435)

### M24 · Retire the SQLite publication helper used by tests and benchmarks

Only tests and a benchmark call `SQLiteStore.publication()`. It implements conditional insertion and partition bookkeeping, while the metric, provider, and operation writers publish through their own transactions. Move rollback and repeat-publication assertions to those production writers, point the benchmark at a production writer, and remove the unused helper. This keeps the benchmark representative as the writers evolve. [Helper](src/lyra/runtime/sqlite_store.py#L312) · [Foundation test](tests/test_sqlite_foundation.py#L160) · [Benchmark](benchmarks/sqlite_foundation.py#L53) · [Metric writer](src/lyra/runtime/metric_tables.py#L59) · [Provider writer](src/lyra/runtime/provider_records.py#L62) · [Operation writer](src/lyra/runtime/operation_pairs.py#L174)

### M25 · Use one durable run ID for the report and outputs

Lyra allocates an execution ID when an attempt starts and a result reference when it publishes. SQLite stores the report and output description together and enforces a one-to-one relationship between the two identifiers. Use the run ID issued at the start to address either resource after publication. Failed attempts can carry the same kind of ID for diagnostics, and the expiring queue job keeps its own ID. This changes the public API and storage schema. Supporting several independently addressable result sets for one execution would require another identifier. [Execution ID allocation](src/lyra/runtime/report_collection.py#L90) · [Result reference allocation](src/lyra/runtime/execution.py#L1331) · [Storage constraint](src/lyra/runtime/sqlite_schema.py#L215) · [Successful job response](src/lyra/contracts/jobs.py#L309)

### M26 · Use one shared RQ queue per deployment

Lyra picks a queue from the requested metric and submits one job for its whole graph. A dependency assigned to another queue still runs on the worker that picked up the job. The example deployment assigns every metric to `interactive`, with four workers, and reserves two more for `batch`. Those two cannot pick up waiting interactive jobs. [Submission](src/lyra/server/job_submission.py#L125) · [Graph execution test](tests/test_worker_graph_integration.py#L135) · [Example configuration](examples/lyra-deployment/lyra.toml#L44)

Send all jobs to one shared RQ queue and keep worker concurrency configurable. Replace per-metric overrides, queue allowlists, and named pool settings with a single worker configuration. Remove queue assignments from the catalog and its fingerprint, and use one worker service in the example deployment. Update the admin API, CLI, and operator docs directly for the closed alpha. Report queue depth, worker heartbeats, and jobs by status without requiring a queue selector. [Routing configuration](src/lyra/contracts/config.py#L324) · [Catalog fingerprint](src/lyra/server/registry.py#L48) · [Worker concurrency](src/lyra/server/worker_launcher.py#L83) · [Compose services](examples/lyra-deployment/compose.yml#L29) · [Admin queue inspection](src/lyra/server/routes/admin.py#L249)

Sharing capacity means a short job can wait when long jobs occupy every worker. Revisit separate pools if a measured wait-time requirement or a workload that needs dedicated hardware justifies reserving workers.

## Migration check

The review found no database migration chain or reader for earlier config formats. SQLite initializes the current schema with `create_all`, and the config model requires schema version 5. The compatibility work identified above concerns internal representations and fixtures. [SQLite initialization](src/lyra/runtime/sqlite_store.py#L187) · [Config version](src/lyra/contracts/config.py#L501)
