# Capability contracts for plugin authors

Read this reference when choosing source identity, registering concrete metrics, offering canonical selection, or defining revision coverage. It complements the [adapter and resource contract](authoring.md).

## Geographic units and source observations

Identify a geographic unit by its collection, unit type, geographical edition, and string ID. Equal IDs in different geographical editions imply no correspondence. Two observations of the same geographic unit can have different attributes and definitions; they are not interchangeable input data.

Build a shared `LocationFamily` with `schema.family(collection, unit_type)`. Its structure covers columns, types, nullability, constraints, ID/geometry conventions, and units. Reuse that family in concrete `LocationContract` declarations:

| Field | Meaning |
| --- | --- |
| `family` | Shared collection, unit type, and structural schema |
| `source` | Identifier distinguishing concrete source observations |
| `geographical_edition` | Geographic boundaries and unit identities |
| `edition` | Optional named source-contract edition |
| `meaning` | Description of the source data and its interpretation |
| `observation_period` | Optional period described by the observations |
| `release` | Optional source release |
| `field_definitions` | Meanings keyed by declared field names |
| `data_revision` | Optional maintained data version for this concrete contract |

A concrete reference includes collection, unit type, source, geographical edition, and optional source edition. Choose identities that distinguish source observations, including distinct periods or releases. Descriptive metadata alone does not create a new source identity. Several periods may use one geography; source edition, release, period, and geographical edition remain distinct.

Provider-backed inputs and outputs reuse the provider’s contracts. Map every named `Locations` argument through `locations={name: contract}`; operations also supply `output_contract`. Handlers receive ordinary resolved location values. Preserve column definitions and source meaning even when schemas match: shared structure does not establish statistical comparability.

## Concrete metrics and private intermediates

Use ordinary Python factories to create concrete graphs. Give each decorated metric a unique `name` and register every generated handler in `PluginDefinition`. Each metric owns its parameter, input, and output contracts. Bind dependencies by producer name and named output.

Mark intermediate metrics `private=True` to exclude them from public discovery and submissions. Lyra still executes them as dependencies. For development, call `runner.run_development()` with the metric name, named ID selections through `inputs=`, and settings through `parameters=`. Dependency inspection can show private nodes.

## Public selection and calculation methodology

Public requests use these values across local execution, Python clients, HTTP, and Model Context Protocol (MCP):

- `metric`: the registered public metric name
- `inputs`: named source ID selections
- `parameters`: ordinary calculation values
- `cache_revision`: an optional value to control cache reuse

For local runs and Python client job methods, pass the registered metric name first. Supply selections and calculation settings through keyword-only `inputs=None` and `parameters=None`. Python `None` means omission.

HTTP `POST /jobs` accepts the shared request as its body; MCP `lyra_run_metric` accepts it as tool arguments. Lyra treats omitted mappings as empty mappings and still checks required selections and parameters.

Lyra rejects explicit JSON null sections and unknown envelope fields. Values inside `parameters` may be null only where the metric schema permits them. Unknown metric names fail, and public submissions reject private roots.

Each selection contains `collection`, `unit_type`, and `ids`. Supply at least one ID, with each ID a nonempty string. The metric binds each input to one source contract for each accepted collection and unit type. Catalog compilation rejects ambiguous bindings.

For several reachable contracts sharing a source scope, declare `source_bindings={input_name: (source_contract.reference,)}` on the metric. Bindings must be reachable, unique per source scope, and preserve the native contract for direct native selection. Unambiguous sources need no override.

Opt an operation into automatic routing with `canonical=True`. It must have exactly one named location-set input and one location-set output. Resources, context, fixed/default parameters, and internal dependencies are allowed when satisfiable from that input and declared bindings without extra caller arguments. Canonical selectors use their own defaults/fixed bindings, not matching metric parameters.

A canonical selector accepts a batch and returns the union of related targets, each identity once in deterministic order, as complete rows under its output contract. It can obtain all rows in one query without separate acquisition. Empty intermediates continue to handlers, which must support them. Retain source-to-target membership explicitly when methodology needs it; set selection does not imply grouping or aggregation.

Canonical operations are optional. Explicit dependencies can use noncanonical operations, and other enabled plugins may supply compatible canonical routes. Without a route, a public input accepts its native type only. Requests do not name operations or construct pipelines.

Lyra chooses the route with the fewest operations across the enabled catalog. An exact contract match requires zero operations. For shortest-path ties, register ordered decorated operation references through `PluginDefinition(route_preferences=[(first_operation, second_operation)])`. Each preference applies to its inferred concrete source/target pair across metrics. It must remain canonical, connected, shortest, and nonconflicting. Invalid preferences fail catalog compilation. A preference may name the only shortest path.

Opposite-direction edges do not imply inverses. Conversion-graph cycles are allowed, while accepted execution plans must be acyclic. Same-contract inputs never expand through a round trip. Alternative paths need not yield equivalent locations; adding a shortcut may change results. Execution keeps the accepted route instead of rerouting after failure or toward warmer caches.

Canonical selection resolves native inputs before ordinary dependency forwarding. Every `Input(...)` sees the effective inputs of its declaration owner. Explicit bindings override matching-name forwarding, then producer parameter defaults fill remaining fields. Internal type mismatches require explicit compatible bindings; public routing does not replace methodology.

## Revisions, cache reuse, and pinning

Set `data_revision` on location definitions and document its coverage. It covers the dataset’s geometry, attributes, and included relationships, even across files or tables. Unrelated reads by a handler are not implicitly covered. A correction can advance the revision without changing geographic identity or introducing a selectable edition. Equal revision strings on different contracts do not identify the same dataset.

Metrics and operations inherit data revisions from native inputs and transitive explicit dependencies. Location-producing operations also include the target contract’s revision. You don’t need to repeat transitive data revisions. Purely derived locations can rely on declared dependencies without an independent data revision.

Declare `code_revision` on every metric and operation to identify its implementation independently of dataset revisions. Advance this revision when implementation behavior changes.

Omitted revisions mean unversioned, including for implicit editions. Lyra records the missing revision in provenance and can still reuse matching content. You must track changes to unversioned sources. Use `cache_revision` for external changes outside location-revision coverage, unversioned implementation changes, or deliberate recomputation. It adds an identity component to every node in that run alongside location revisions and computation identity. Repeating a value permits reuse; a fresh value preserves earlier entries rather than purging a server cache.

Preparation pins declared revisions before local acquisition or remote acceptance. Providers must serve the requested revision or fail clearly; never substitute newer data. Pinning metadata cannot detect unreported changes or create storage snapshots. Historical retention is not required: failing an outdated queued job is valid.

The runtime resolves canonical selections before it checks the metric cache. It can reuse a complete, ordered batch when native contents, concrete contracts, effective parameters, calculation revisions, and computation identity match. A lookup can still require acquisition or intermediate reads. Changing the order or membership of the resolved native batch requires a separate lookup.

A revision used only to select locations affects the selector’s cache. Changing a native dataset or explicit dependency revision invalidates the metric even when the values match.

For metric and operation identity, Lyra combines `(name, code_revision)` with semantic contracts, dependencies, and protocol versions. Current provider identity also includes its installed distribution version and source contracts. Editing source code alone does not change a node’s code revision.

## Run and inspect the contract

Call `runner.run()` with the metric name, `inputs=`, and `parameters=` to execute a public metric. It returns native DataFrames or GeoDataFrames keyed by output name. Call `runner.inspect()` separately to compare the accepted plan with what ran.

The plan records the original selections, effective parameters, concrete metric, routes, and pinned revisions. Execution observations record native input counts and fingerprints. Use those observations to distinguish a completed calculation from a cache hit. Even when Lyra reuses tables, provenance describes the current run.

On a server, search results show which source types each metric accepts. Inspect the selected metric before submitting:

- Check parameter constraints and defaults in `parameters`.
- Read the result schemas in `outputs`.
- Choose source types from `accepted_selections` and inspect their `bindings`.
- Read `computed_inputs` to understand dependency inputs; don’t include them in the request.

Submit once and poll the returned job handle. When execution finishes, preview the results or download them with your agent credentials.

Malformed or unsupported requests fail before acquisition or job creation. Unknown IDs fail execution with input, source scope, and unresolved IDs; an existing source with no related targets continues as an empty selection. Inspection can succeed for a failed remote job. Read the returned job status to distinguish execution failure from a failed inspection request.

Use MCP `lyra_get_metric_dependencies` to inspect a public metric’s direct dependencies and consumers. It returns up to 50 relationships and reports how many it omits. Each relationship identifies:

- Private nodes
- Producer outputs
- Receiving input names

The tool includes declared operation dependencies. It excludes possible canonical routes because those routes are not calculation dependencies. You don’t need this tool for routine execution or successful result interpretation.

Use the contracts above to adapt your calculation without a Lyra checkout or live service. To exercise the client locally, follow [Run the location examples](https://github.com/RodolfoFigueroa/lyra/blob/main/examples/location-plugin/USAGE.md), which uses an in-process fake transport.

## Deployment compatibility

API and workers use the same live definitions to prepare requests. Both processes import enabled distributions through their entry-point factories, so every plugin dependency must work in both. Keep loading and request preparation free of external input/output (I/O).

Deploy one immutable build and share its `build_revision` across API and workers. The revision covers executable code, declarations, and routing policy. Each queued request records:

- Normalized selections and complete effective values for each node use
- Pinned location scopes
- The build revision

Workers reject a different build with `deployment_changed` before acquiring resources or running handlers; you can resubmit. Compatible workers prepare the request against their live definitions. The executor validates the complete plan before acquiring resources.

The public discovery fingerprint describes metadata. Author `code_revision` values identify metric and operation implementations for computation reuse. A build revision is not a cache invalidation token.
